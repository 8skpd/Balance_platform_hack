"""Build a reproducible source bundle without accounts, uploads or organizer material."""
import argparse
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED
ROOT=Path(__file__).resolve().parents[1]

def files():
    for name in ('README.md','.gitignore','.env.example','.env.docker.example','Dockerfile','.dockerignore','compose.yaml'):
        yield ROOT/name
    for name in ('backend','frontend/src','frontend/public','frontend/tests','scripts','docs','docker'):
        for path in sorted((ROOT/name).rglob('*')):
            if path.is_file() and not any(p in ('__pycache__','.pytest_cache') for p in path.parts) and path.suffix not in ('.pyc','.tsbuildinfo'):
                yield path
    for name in ('package.json','package-lock.json','index.html','tsconfig.json','vite.config.ts','playwright.config.ts'):
        yield ROOT/'frontend'/name

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT/'artifacts/balance-source-0.11.0.zip');args=parser.parse_args()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with ZipFile(args.output,'w',ZIP_DEFLATED) as archive:
        for path in files():archive.write(path,path.relative_to(ROOT))
    print(args.output)
if __name__=='__main__':main()
