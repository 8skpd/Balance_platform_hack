"""CSV and XML snapshots converted to the existing per-application document contract."""
import csv
import io
import re
import xml.etree.ElementTree as ET

ID_COLUMNS = ('application_id', 'APP_ID', 'appData.application_id')
NUMBER = re.compile(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z')


def scalar(text):
    """Infer JSON scalars, preserving whitespace, leading zeroes and ordinary text."""
    from .imports import strict_json
    if text in ('true', 'false', 'null') or NUMBER.fullmatch(text) or text.startswith(('[', '{', '"')):
        return strict_json(('{"value":'+text+'}').encode(), 'Значение')[0]['value']
    return text


def csv_documents(raw, location, combined=False):
    from .settings import IMPORT_MAX_PAIRS
    binary = io.BytesIO(raw) if isinstance(raw,bytes) else raw
    try:
        text = io.TextIOWrapper(binary,encoding='utf-8-sig',newline='')
        header_line = text.readline(2*1024**2+1)
        if len(header_line)>2*1024**2:raise ValueError('Слишком длинная строка заголовков CSV')
        try:
            delimiter = csv.Sniffer().sniff(header_line, delimiters=',;\t').delimiter
        except csv.Error:
            delimiter = ','
        csv.field_size_limit(16 * 1024**2)
        from itertools import chain
        reader = csv.reader(chain([header_line],text), delimiter=delimiter, strict=True)
        headers = next(reader, [])
        if not headers or len(headers)>1000 or any(not h or h!=h.strip() for h in headers):
            raise ValueError('Ожидается строка заголовков CSV (до 1000 непустых колонок)')
        if len(headers)!=len(set(headers)):
            raise ValueError('Повтор заголовка CSV')
        ids = [h for h in headers if h in ID_COLUMNS]
        if len(ids)!=1:
            raise ValueError('CSV должен содержать одну колонку application_id, APP_ID или appData.application_id')
        side_columns=[h for h in headers if h in ('SOURCE','source','SIDE','side')] if combined else []
        if combined and len(side_columns)!=1:raise ValueError('Общий CSV должен содержать одну колонку SOURCE или SIDE со значениями BEFORE / AFTER')
        paths = [(['appData','application_id'] if h==ids[0] else h.split('.')) for h in headers]
        all_paths = set(tuple(p) for p in paths)
        for parts in paths:
            if len(parts)>64 or any(not p or len(p)>256 for p in parts) or len('.'.join(parts))>2048:
                raise ValueError('Некорректный путь колонки CSV')
            if any(tuple(parts[:n]) in all_paths for n in range(1,len(parts))):
                raise ValueError('Конфликт путей колонок CSV: '+'.'.join(parts))
        count = 0
        for cells in reader:
            if not cells:
                continue
            count += 1
            if count>IMPORT_MAX_PAIRS*(2 if combined else 1):
                raise ValueError(f'В CSV больше {IMPORT_MAX_PAIRS} заявок')
            if len(cells)!=len(headers):
                raise ValueError(f'Строка {reader.line_num}: число значений не совпадает с числом колонок')
            doc = {};side=None
            for header, parts, cell in zip(headers, paths, cells):
                if header in side_columns:
                    side=side_name(cell)
                    continue
                node = doc
                for part in parts[:-1]:
                    node = node.setdefault(part, {})
                # An ID is text even when it contains only digits.
                node[parts[-1]] = cell if header==ids[0] else scalar(cell)
            result=(doc,None,f'{location}, строка {reader.line_num}')
            yield (*result,side) if combined else result
        if not count:
            raise ValueError('В CSV нет заявок')
    except UnicodeError as exc:
        raise ValueError(f'{location}: CSV должен быть в кодировке UTF-8') from exc
    except csv.Error as exc:
        raise ValueError(f'{location}: некорректный CSV, проверьте разделители и кавычки ({exc})') from exc
    except (ValueError, RecursionError) as exc:
        raise ValueError(f'{location}: {exc}') from exc
    finally:
        binary.close()


class BoundedTree(ET.TreeBuilder):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.nodes = 0

    def doctype(self, name, pubid, system):
        raise ValueError('DTD и объявления сущностей XML запрещены')

    def start(self, tag, attrs):
        self.depth += 1
        self.nodes += 1 + len(attrs)
        if self.depth>66 or self.nodes>1_000_000:
            raise ValueError('Превышена сложность XML')
        if len(tag)>2048 or any(len(k)>2048 for k in attrs):
            raise ValueError('Слишком длинное имя XML')
        return super().start(tag, attrs)

    def end(self, tag):
        self.depth -= 1
        return super().end(tag)


def local_name(name):
    name = name.rsplit('}',1)[-1]
    if not name or '.' in name or len(name)>256:
        raise ValueError('Некорректное имя поля XML: '+name[:100])
    return name


def xml_value(element):
    children = list(element)
    text = element.text or ''
    kind = element.get('type')
    typed = kind in ('string','number','boolean','null','object','array')
    nil = element.get('{http://www.w3.org/2001/XMLSchema-instance}nil')
    attrs = {}
    for key,value in element.attrib.items():
        if (key=='type' and typed) or key=='{http://www.w3.org/2001/XMLSchema-instance}nil':
            continue
        name = '@'+local_name(key)
        if name in attrs:
            raise ValueError('Конфликт пространств имён атрибутов XML')
        attrs[name] = value
    if nil not in (None,'true','false','1','0'):
        raise ValueError('Некорректное значение xsi:nil')
    if kind=='null' or nil in ('true','1'):
        if children or text.strip() or attrs:
            raise ValueError('Пустое значение XML содержит данные')
        return None
    if any((child.tail or '').strip() for child in children) or (children and text.strip()):
        raise ValueError('Смешанный текст и элементы XML не поддерживаются')
    if kind=='array' or (children and all(local_name(c.tag)=='item' for c in children)):
        if attrs or text.strip() or any(local_name(c.tag)!='item' for c in children):
            raise ValueError('Массив XML должен содержать только элементы item')
        return [xml_value(child) for child in children]
    if children or attrs or kind=='object':
        if typed and kind!='object':
            raise ValueError('Скаляр XML не может содержать вложенные элементы или атрибуты')
        out = dict(attrs)
        groups = {}
        for child in children:
            name = local_name(child.tag)
            group = groups.setdefault(name, [])
            if group and group[0].tag!=child.tag:
                raise ValueError('Конфликт пространств имён элементов XML')
            group.append(child)
        for name,group in groups.items():
            out[name] = xml_value(group[0]) if len(group)==1 else [xml_value(c) for c in group]
        if text.strip():
            out['#text'] = scalar(text)
        return out
    if kind=='string' or local_name(element.tag)=='application_id':
        return text
    value = scalar(text)
    if kind=='number' and (isinstance(value,bool) or not isinstance(value,(int,float))):
        raise ValueError('Ожидается число XML')
    if kind=='boolean' and not isinstance(value,bool):
        raise ValueError('Ожидается true или false в XML')
    return value


SIDES={'before':'BEFORE_MASTER','master':'BEFORE_MASTER','before_master':'BEFORE_MASTER',
       'after':'AFTER_BETA','beta':'AFTER_BETA','after_beta':'AFTER_BETA'}

def side_name(value):
    result=SIDES.get(value.strip().lower())
    if not result:raise ValueError('Версия должна быть BEFORE / AFTER или MASTER / BETA')
    return result


def xml_document(element):
    doc=xml_value(element)
    if not isinstance(doc,dict):raise ValueError('Заявка XML должна содержать поля')
    nested=doc.get('appData',{})
    if not isinstance(nested,dict):raise ValueError('appData должен быть объектом')
    candidates=[v for v in (nested.get('application_id'),nested.get('@application_id'),doc.get('application_id'),doc.get('@application_id'),doc.get('@id')) if v is not None]
    if any(isinstance(v,(dict,list,bool)) for v in candidates):raise ValueError('Некорректный application_id XML')
    if len({str(v) for v in candidates})>1:raise ValueError('Противоречивые идентификаторы заявки XML')
    if candidates:
        nested=dict(nested);nested.pop('@application_id',None);nested['application_id']=str(candidates[0]);doc['appData']=nested
        for key in ('application_id','@application_id','@id'):doc.pop(key,None)
    return doc


def xml_documents(raw,location,fallback_id,combined=False):
    # Expat feeds one bounded record tree at a time: a large collection is never materialized.
    from xml.parsers import expat
    from collections import deque
    from .settings import IMPORT_MAX_PAIRS
    binary=io.BytesIO(raw) if isinstance(raw,bytes) else raw
    parser=expat.ParserCreate(namespace_separator='}')
    depth=0;mode=None;builder=None;record_depth=0;count=0;side=None;seen_sides=set();queued=deque();record_chars=0
    def expanded(name):return '{'+name if '}' in name else name
    def forbidden(*args):raise ValueError('DTD и объявления сущностей XML запрещены')
    parser.StartDoctypeDeclHandler=forbidden;parser.EntityDeclHandler=forbidden;parser.ExternalEntityRefHandler=forbidden
    def start(tag,attrs):
        nonlocal depth,mode,builder,record_depth,side,record_chars
        depth+=1;tag=expanded(tag);attrs={expanded(k):v for k,v in attrs.items()}
        if depth>66:raise ValueError('Превышена сложность XML')
        name=local_name(tag).lower()
        if depth==1:
            mode='combined' if combined else 'collection' if name in ('applications','records','rows') else 'single'
            if combined and name not in ('dataset','comparison'):raise ValueError('Общий XML должен иметь корень dataset с разделами BEFORE / AFTER')
            if mode!='single' and attrs:raise ValueError('Корень списка XML должен содержать только заявки')
        if mode=='combined' and depth==2:
            side=side_name(name)
            if side in seen_sides or attrs:raise ValueError('Повтор или некорректный раздел BEFORE / AFTER')
            seen_sides.add(side)
        target=1 if mode=='single' else 3 if mode=='combined' else 2
        if depth==target:
            if mode!='single' and name not in ('application','record','row'):raise ValueError('В списке XML ожидаются элементы application, record или row')
            builder=BoundedTree();record_depth=depth;record_chars=0
        if builder is not None:builder.start(tag,attrs)
    def text(value):
        nonlocal record_chars
        if builder is None:
            if value.strip():raise ValueError('Корень списка XML должен содержать только заявки')
        else:
            record_chars+=len(value)
            if record_chars>16*1024**2:raise ValueError('Слишком большая заявка XML')
            builder.data(value)
    def end(tag):
        nonlocal depth,builder,count
        if builder is not None:
            builder.end(expanded(tag))
            if depth==record_depth:
                element=builder.close();builder=None;count+=1
                if count>IMPORT_MAX_PAIRS*(2 if combined else 1):raise ValueError('В XML слишком много заявок')
                result=(xml_document(element),fallback_id if mode=='single' else None,f'{location}, заявка {count}')
                queued.append((*result,side) if combined else result)
        depth-=1
    parser.StartElementHandler=start;parser.EndElementHandler=end;parser.CharacterDataHandler=text
    try:
        while True:
            chunk=binary.read(65536)
            parser.Parse(chunk,not chunk)
            while queued:yield queued.popleft()
            if not chunk:break
        if not count:raise ValueError(f'XML должен содержать от 1 до {IMPORT_MAX_PAIRS} заявок')
        if combined and seen_sides!=set(SIDES.values()):raise ValueError('Общий XML должен содержать BEFORE и AFTER')
    except (expat.ExpatError,ValueError,RecursionError,LookupError) as exc:
        raise ValueError(f'{location}: {exc}') from exc
    finally:binary.close()
