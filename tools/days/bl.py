import sqlite3,re,sys
db=sqlite3.connect('data/articles.db')
txt=open('data/editor/BACKLOG.md').read()
ids=[int(x) for x in re.findall(r'\b(1[345]\d{3})\b',txt.split('## 2026-10-19')[1])]
pat=re.compile(sys.argv[1],re.I)
for i in ids:
    r=db.execute('select id,source,date_local,title,text from articles where id=?',(i,)).fetchone()
    if pat.search(r[3]) or (len(sys.argv)>2 and pat.search(r[4][:600])):
        print(r[0],r[1][:8],r[2][5:16],re.sub(r'\s+',' ',r[3])[:140])
