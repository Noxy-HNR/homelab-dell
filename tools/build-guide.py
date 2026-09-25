"""Render the beginner Markdown guide into browsable, offline HTML."""
from pathlib import Path
import html
import re
import markdown

ROOT=Path(__file__).resolve().parents[1]
DOCS=ROOT/'docs'
PAGES=['START-HERE.md','INSTALL.md','setup/HP.md','setup/DELL.md','setup/WINDOWS.md','DAILY-USE.md','GAME-DRIVE.md','DELL-NETWORK.md','PARTS.md','STACK.md','VALIDATION.md','features/MODELS.md','features/WORKBENCH.md','features/OPERATIONS.md']
OUTPUT={name:('guide.html' if name=='START-HERE.md' else name[:-3]+'.html') for name in PAGES}
CSS='''
:root{font-family:Segoe UI,system-ui,sans-serif;color:#24333c;background:#f5f4ef}*{box-sizing:border-box}body{margin:0}aside{position:fixed;top:0;bottom:0;width:235px;background:#15302c;color:#d7e8dd;padding:30px 22px;overflow:auto}aside strong{display:block;font-size:23px;margin:0 0 8px}aside p{font-size:12px;color:#adcbc0;line-height:1.5}aside a{display:block;color:#e0eee7;text-decoration:none;padding:12px 10px;border-radius:5px;margin:3px 0;font-size:14px}aside a:hover{background:#284c40}main{margin-left:235px;max-width:1160px;padding:45px 65px 70px;background:#fff;min-height:100vh}h1{font-size:36px;letter-spacing:-.04em;line-height:1.16;margin:0 0 30px;color:#173c31}h2{font-size:24px;margin-top:42px;padding-top:12px;border-top:1px solid #e1e8e3;color:#234c3c}h3{font-size:18px;margin-top:28px}p,li{font-size:16px;line-height:1.7}li{margin-bottom:8px}a{color:#176a51}img{display:block;max-width:100%;height:auto;border:1px solid #d5e0d9;border-radius:8px;margin:24px auto}img[src$="imagestream-card.jpg"]{max-height:430px;width:auto}pre{position:relative;background:#14232a;color:#e9f3ed;padding:22px;border-radius:8px;overflow:auto;line-height:1.6;font-size:13px}pre code{background:none;color:inherit;padding:0;font-size:13px}code{background:#edf2ee;color:#274e3f;padding:2px 5px;border-radius:3px;font-size:13px;overflow-wrap:anywhere}table{border-collapse:collapse;width:100%;font-size:14px;margin:22px 0}th{background:#edf3ee;text-align:left;color:#244d3c}th,td{border:1px solid #dbe4dd;padding:12px;line-height:1.5}blockquote{margin:22px 0;border-left:4px solid #76a486;background:#edf5ef;padding:10px 20px}.topbar{display:flex;justify-content:space-between;color:#65796c;font-size:12px;margin-bottom:30px}.topbar button{background:#e1eddf;border:0;padding:8px 12px;border-radius:5px;cursor:pointer}pre button{position:absolute;right:8px;top:8px;border:1px solid #49655b;background:#243d34;color:#dff0df;border-radius:4px;padding:4px 9px;cursor:pointer}footer{border-top:1px solid #dbe4dd;color:#667c6d;font-size:12px;margin-top:40px;padding-top:20px}@media(max-width:800px){aside{position:static;width:auto}aside a{display:inline-block}main{margin:0;padding:28px 20px}h1{font-size:30px}table{display:block;overflow:auto}}@media print{aside,.topbar,pre button{display:none}main{margin:0;padding:0;max-width:none}pre{white-space:pre-wrap;background:#f0f0f0;color:#000}img{max-height:650px;page-break-inside:avoid}a{color:#000}h2,h3{break-after:avoid}}
'''
NAV=[('START-HERE.md','Start here'),('INSTALL.md','1 · Hardware & Ubuntu'),('setup/HP.md','2 · HP NAS'),('setup/DELL.md','3 · Dell workbench'),('setup/WINDOWS.md','4 · Windows PCs'),('GAME-DRIVE.md','5 · Steam drive'),('DAILY-USE.md','Use your homelab'),('PARTS.md','Parts to buy'),('VALIDATION.md','What is tested')]
(DOCS/'assets/guide.css').write_text(CSS,encoding='utf-8')
(DOCS/'assets/guide.js').write_text("document.getElementById('print').onclick=()=>window.print();document.querySelectorAll('pre').forEach(p=>{const b=document.createElement('button');b.textContent='Copy';b.onclick=async()=>{try{await navigator.clipboard.writeText(p.querySelector('code').textContent);b.textContent='Copied';}catch{b.textContent='Select and copy';}};p.appendChild(b);});",encoding='utf-8')
for source in PAGES:
    path=DOCS/source
    if not path.exists():
        raise SystemExit(f'Missing guide page: {source}')
    out=DOCS/OUTPUT[source]
    body=markdown.markdown(path.read_text(encoding='utf-8'),extensions=['extra','toc','sane_lists'])
    def rewrite(match):
        href=match.group(1)
        if '://' in href or href.startswith(('#','mailto:')):return match.group(0)
        target,separator,fragment=href.partition('#')
        absolute=(path.parent/target).resolve()
        try: relative=absolute.relative_to(DOCS).as_posix()
        except ValueError:return match.group(0)
        if relative in OUTPUT:
            import os
            converted=os.path.relpath(DOCS/OUTPUT[relative],out.parent).replace('\\','/')
            return f'href="{converted}{separator}{fragment}"'
        return match.group(0)
    body=re.sub(r'href="([^"]+)"',rewrite,body)
    import os
    nav=''.join(f'<a href="{os.path.relpath(DOCS/OUTPUT[p],out.parent).replace(chr(92),"/")}">{label}</a>' for p,label in NAV)
    title=re.search(r'<h1[^>]*>(.*?)</h1>',body)
    name=re.sub('<[^>]+>','',title.group(1)) if title else 'Homelab setup'
    page=f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(name)} · Homelab</title><style>{CSS}</style></head><body><aside><strong>Homelab</strong><p>Your setup guide.<br>One machine at a time.</p>{nav}</aside><main><div class="topbar"><span>INSTALL · USE · RECOVER</span><button onclick="window.print()">Print this page</button></div>{body}<footer>Built from the Markdown guide in Noxy-HNR/homelab-stack. Example screenshots use local test data; the physical server checks happen during setup.</footer></main><script>document.querySelectorAll('pre').forEach(p=>{{const b=document.createElement('button');b.textContent='Copy';b.onclick=async()=>{{try{{await navigator.clipboard.writeText(p.querySelector('code').textContent);b.textContent='Copied';}}catch{{b.textContent='Select and copy';}}}};p.appendChild(b);}});</script></body></html>'''
    css_url=os.path.relpath(DOCS/'assets/guide.css',out.parent).replace('\\','/')
    js_url=os.path.relpath(DOCS/'assets/guide.js',out.parent).replace('\\','/')
    page=re.sub(r'<style>.*?</style>',f'<link rel="stylesheet" href="{css_url}">',page,flags=re.S)
    page=page.replace('onclick="window.print()"','id="print"')
    page=re.sub(r'<script>.*?</script>',f'<script src="{js_url}"></script>',page,flags=re.S)
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(page,encoding='utf-8')
print(f'Built {len(PAGES)} HTML pages. Open docs/guide.html.')
