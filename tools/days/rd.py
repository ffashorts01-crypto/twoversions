import sqlite3,sys,re
db=sqlite3.connect('/workspace/compare-site-v2/data/articles.db')
n=int(sys.argv[-1]) if sys.argv[-1].startswith('+') is False and len(sys.argv)>2 and sys.argv[-1].isdigit() and int(sys.argv[-1])<20000 and False else 2500
for i in sys.argv[1:]:
    r=db.execute('select id,source,date_local,title,text,url from articles where id=?',(int(i),)).fetchone()
    print('=====',r[0],r[1],r[2],r[5]);print('T:',r[3]);print(re.sub(r'\s+',' ',r[4])[:n]);print()
