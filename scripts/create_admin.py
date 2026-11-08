"""Create the first administrator locally; never exposes public registration."""
import getpass
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from pydantic import ValidationError
from sqlalchemy import select,func
from app import auth,store

def main():
    store.init()
    with store.engine.connect() as conn:
        if conn.execute(select(func.count()).select_from(store.users)).scalar_one():
            raise ValueError('Аккаунты уже существуют. Создайте пользователя в разделе «Администрирование».')
    username=input('Логин администратора: ')
    name=input('Имя: ')
    password=getpass.getpass('Пароль (не менее 10 символов): ')
    if password!=getpass.getpass('Повторите пароль: '):raise ValueError('Пароли не совпадают')
    account=auth.bootstrap_admin(auth.Registration(username=username,display_name=name,password=password))
    print('Администратор '+account['username']+' создан. Войдите в приложение.')

if __name__=='__main__':
    try:main()
    except ValidationError as exc:
        print('Ошибка: '+'; '.join(e['msg'] for e in exc.errors(include_input=False)),file=sys.stderr);sys.exit(1)
    except (ValueError,EOFError) as exc:
        print('Ошибка: '+str(exc),file=sys.stderr);sys.exit(1)
    except KeyboardInterrupt:sys.exit(130)
