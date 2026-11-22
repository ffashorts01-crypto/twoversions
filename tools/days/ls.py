import sqlite3,sys,re
db=sqlite3.connect('/workspace/compare-site-v2/data/articles.db')
lo=int(sys.argv[1]); hi=int(sys.argv[2]) if len(sys.argv)>2 else 10**9
day=sys.argv[3] if len(sys.argv)>3 else None
q='select id,source,grp,date_local,title,military,length(text) from articles where id>=? and id<=?'
for r in db.execute(q+' order by date_utc',(lo,hi)):
    if day and not r[3].startswith(day): continue
    print(r[0],r[1][:8],r[3][5:16],'M' if r[5] else '-',r[6],re.sub(r'\s+',' ',r[4])[:150])
