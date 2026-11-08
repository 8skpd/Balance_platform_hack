#!/usr/bin/env python3
"""Reproducible synthetic strategy snapshots. Contains no real customer data."""
import argparse
import csv
import io
import json
import zipfile
from pathlib import Path


def document(index, after):
    score=400+(index*37)%450
    changed_score=score+(20 if after and index%11==0 else 0)
    decision='APPROVE' if changed_score >= (620 if after else 600) else 'DECLINE'
    if after and index%19==0:decision='DECLINE'
    trial={'product_id':['LOAN','CARD','MORTGAGE'][index%3],
           'scoring':{'pd':{'modelName':'pd-v2' if after and index%17==0 else 'pd-v1'}},
           'cutOff':{'name':'tight' if after and index%29==0 else 'base'},
           'rules':[{'name':'debt_limit','hit':index%7==0 and not(after and index%13==0)}]}
    if after and index%31==0:trial['experimental_flag']=True
    return [f'LOAD{index+1:07d}',decision,changed_score,json.dumps([trial],ensure_ascii=False,separators=(',',':'))]


def generate(output,pairs=500000,shard_size=10000):
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    expected={'total_apps':pairs,'changed_apps':0,'decision_changed':0,'score_changed':0,'model_changed':0,'rule_turned_off':0,'attribute_added':0}
    temporary=output.with_suffix('.part')
    with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED,compresslevel=1,allowZip64=True) as archive:
        for after,side in [(False,'BEFORE_MASTER'),(True,'AFTER_BETA')]:
            for start in range(0,pairs,shard_size):
                path=f'RESULTS/{side}/part-{start//shard_size+1:04d}.csv'
                with io.TextIOWrapper(archive.open(path,'w',force_zip64=True),encoding='utf-8',newline='') as stream:
                    writer=csv.writer(stream);writer.writerow(['application_id','decisionResult.decision','characteristics.score_okb','decisionResult.product_trials'])
                    for index in range(start,min(start+shard_size,pairs)):
                        row=document(index,after);writer.writerow(row)
                        if after:
                            before=document(index,False)
                            expected['changed_apps']+=row!=before
                            expected['decision_changed']+=row[1]!=before[1]
                            expected['score_changed']+=index%11==0
                            expected['model_changed']+=index%17==0
                            expected['rule_turned_off']+=index%91==0
                            expected['attribute_added']+=index%31==0
        manifest={'name':f'Нагрузочный набор — {pairs:,} заявок'.replace(',',' '),'synthetic':True,'generator_version':1,'pairs':pairs,'snapshots':2,'seed':'deterministic-modular-v1','expected':expected}
        archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    temporary.replace(output)
    result={**manifest,'archive':str(output.resolve()),'bytes':output.stat().st_size}
    output.with_suffix('.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--pairs',type=int,default=500000);parser.add_argument('--shard-size',type=int,default=10000);parser.add_argument('--output',default='artifacts/load-500000.zip');args=parser.parse_args()
    if not 1<=args.pairs<=500000 or not 1<=args.shard_size<=20000:parser.error('pairs: 1..500000; shard-size: 1..20000')
    print(json.dumps(generate(args.output,args.pairs,args.shard_size),ensure_ascii=False))
