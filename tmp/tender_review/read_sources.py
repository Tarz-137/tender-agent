import sys, json, hashlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent/'deps'))
import xlrd, openpyxl
from pypdf import PdfReader
root=Path(r'C:\Users\User\OneDrive\Desktop\PQ_Criteria')
out=Path(__file__).parent
for p in sorted(root.glob('*.pdf')):
    reader=PdfReader(p)
    pages=[page.extract_text() or '' for page in reader.pages]
    (out/(p.stem+'.txt')).write_text('\n\n'.join(f'PAGE {i+1}\n{t}' for i,t in enumerate(pages)),encoding='utf-8')
    print(p.name, 'pages',len(pages),'chars',[len(t) for t in pages], 'hash',hashlib.sha256(p.read_bytes()).hexdigest()[:12])
for p in [root/'General Check List.xlsx',*sorted((root/'BOQ').glob('*.xls'))]:
    lines=[]
    if p.suffix=='.xls':
        wb=xlrd.open_workbook(p)
        sheets=[(s.name, [[s.cell_value(r,c) for c in range(s.ncols)] for r in range(s.nrows)]) for s in wb.sheets()]
    else:
        wb=openpyxl.load_workbook(p,data_only=False)
        sheets=[(s.title,[[c.value for c in row] for row in s]) for s in wb]
    for name,rows in sheets:
        lines.append('SHEET '+name)
        for i,row in enumerate(rows):
            cells=[f'{openpyxl.utils.get_column_letter(j+1)}{i+1}: {v}' for j,v in enumerate(row) if v is not None and v!='']
            if cells: lines.append(' | '.join(cells))
    text='\n'.join(lines)
    (out/(p.stem+'.txt')).write_text(text,encoding='utf-8')
    print(p.name,[(n,len(r)) for n,r in sheets],len(text))
