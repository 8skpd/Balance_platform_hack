import {Zip, ZipPassThrough, strToU8} from 'fflate';

/** Keep file preparation bounded: no full-file ArrayBuffer or synchronous ZIP compression. */
export async function uploadArchive(files:{path:string;file:File}[],manifest:object,active:()=>boolean):Promise<Blob>{
  const chunks:Blob[]=[];let failure:Error|null=null;let finished=false;
  const zip=new Zip((error,data,final)=>{if(error){failure=error;return;}chunks.push(new Blob([data as BlobPart]));if(final)finished=true;});
  const metadata=new ZipPassThrough('manifest.json');zip.add(metadata);metadata.push(strToU8(JSON.stringify(manifest)),true);
  for(const {path,file} of files){
    const entry=new ZipPassThrough(path);zip.add(entry);const reader=file.stream().getReader();
    try{
      let blocks=0;
      while(true){
        const {done,value}=await reader.read();
        if(!active())throw new DOMException('Передача отменена','AbortError');
        if(failure)throw failure;
        if(done){entry.push(new Uint8Array(),true);break;}
        entry.push(value,false);
        if(++blocks%64===0)await new Promise(resolve=>setTimeout(resolve,0));
      }
    }finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
  }
  zip.end();if(failure)throw failure;if(!finished)throw new Error('Не удалось подготовить файлы');
  return new Blob(chunks,{type:'application/zip'});
}
