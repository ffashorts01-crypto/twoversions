import json,sqlite3,sys,re
db=sqlite3.connect('data/articles.db')
d=json.load(open(f'data/editor/2026-10-{sys.argv[1]}.json'))
cards=d if isinstance(d,list) else d.get('cards',d)
bad=0;cnt=[0]
def walk(o,path):
    global bad
    if isinstance(o,dict):
        if 'id' in o and 'text' in o and isinstance(o['text'],str) and len(o)==2:
            o=dict(o,quote=o['text'])
            cnt[0]+=1
            t=db.execute('select text from articles where id=?',(o['id'],)).fetchone()
            n=lambda s:re.sub(r'\s+',' ',s or '')
            if not t or n(o['quote']) not in n(t[0]): bad+=1; print('BAD',path,o['id'],o['quote'][:60])
        for k,v in o.items(): walk(v,path)
    elif isinstance(o,list):
        for x in o: walk(x,path)
for c in cards: walk(c,c.get('key','?') if isinstance(c,dict) else '?')
print('quotes',cnt[0],'bad',bad)
