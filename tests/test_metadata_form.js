'use strict';
const fs=require('fs'), vm=require('vm'), assert=require('assert');
const source=fs.readFileSync('/var/www/layout-editor/app.js','utf8');
const code=source.slice(source.indexOf('function bukaMetadataArtikel()'),source.indexOf('/* metadata form end */'));
assert(code.includes('function bukaMetadataArtikel'));
const escape=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const decode=s=>s.replace(/&(amp|lt|gt|quot);/g,(_,x)=>({amp:'&',lt:'<',gt:'>',quot:'"'}[x]));
const fixture={terbitan:{edisi:'December'},blocks:[
 ['judul','m-judul','A & B'],['penulis','m-penulis','Jane Smith (1)*'],['afiliasi','m-afiliasi','Institute'],
 ['email','m-email','author@example.com'],['riwayat_diterima','m-riwayat-diterima','30 August 2026'],
 ['riwayat_disetujui','m-riwayat-disetujui','15 October 2026'],['riwayat_terbit','m-riwayat-terbit','6 November 2026'],
 ['abstrak','m-abstrak','An abstract'],['kata_kunci','m-kata-kunci','One\nTwo\nThree'],
 ['sitasi','m-sitasi','Volume: 5\nNomor: 4\nTahun: 2026\nHalaman: 576']
 ].map(([meta_key,id,text])=>({jenis:'meta',meta_key,id,text}))};
function setup(data) {
 const state={artikel:'549-3461-1-LE',data:{terbitan:data.terbitan},blok:Object.fromEntries(data.blocks.map(b=>[b.id,{...b}]))};
 const calls=[], ctx={S:state,esc:escape,kirimBlok:(id,payload)=>calls.push({id,payload}),
  simpanEditor(){},tampilPanel(){},toast(){},tutupModal(){}};
 let html='', nodes={}, fields=[], pubs=[], form={};
 ctx.$=selector=> selector==='#metadata-form'?form : selector==='#metadata-batal'?{}:nodes[selector.slice(1)];
 ctx.$$=selector=>selector==='[data-meta]'?fields:pubs;
 ctx.bukaModal=(text,callback)=>{
  html=text; nodes={};fields=[];pubs=[];form={};
  const tags=/<textarea\b([^>]*)>([\s\S]*?)<\/textarea>|<input\b([^>]*)>/g;
  for(const m of text.matchAll(tags)) {
   const attrs={}; for(const a of (m[1]||m[3]).matchAll(/([\w-]+)="([^"]*)"/g))attrs[a[1]]=decode(a[2]);
   const el={value:m[1]?decode(m[2]):(attrs.value||''),dataset:{},focus(){}};
   if(attrs['data-meta']) {el.dataset.meta=attrs['data-meta'];fields.push(el);}
   if(attrs['data-publication']) {el.dataset.publication=attrs['data-publication'];pubs.push(el);}
   nodes[attrs.id]=el;
  }
  callback({classList:{add(){}},setAttribute(){}});
 };
 vm.createContext(ctx);vm.runInContext(code,ctx);ctx.bukaMetadataArtikel();
 return {state,calls,get html(){return html},nodes,submit:()=>form.onsubmit({preventDefault(){}})};
}
let t=setup(fixture);assert(t.html.includes('Article History'));assert(t.html.includes('Received'));
assert(t.html.includes('Accepted'));assert(t.html.includes('Published'));t.submit();assert.equal(t.calls.length,0);
t=setup(fixture);t.nodes['metadata-riwayat_diterima'].value='31 August 2026';t.submit();
assert.equal(t.calls.length,1);assert.equal(t.calls[0].id,'m-riwayat-diterima');assert.equal(t.calls[0].payload.text,'31 August 2026');
assert.equal(Object.keys(t.calls[0].payload).join(','),'text');
t=setup(fixture);t.nodes['metadata-pub-tahun'].value='2027';t.submit();
assert.equal(t.calls.length,1);assert.equal(t.calls[0].id,'m-sitasi');assert(t.calls[0].payload.text.includes('Edisi: December'));
const malicious=JSON.parse(JSON.stringify(fixture));malicious.blocks[0].text='<script>alert(1)</script>';
t=setup(malicious);assert(!t.html.includes('<script>'));assert(t.html.includes('&lt;script&gt;'));
t=setup(fixture);t.nodes['metadata-riwayat_diterima'].value='Changed';t.state.artikel='different';t.submit();assert.equal(t.calls.length,0);
console.log('Metadata form: 5 JavaScript regression scenarios passed');
if(process.argv[2]&&process.argv[3]) {
 const data=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
 const real=setup(data), css=fs.readFileSync('/var/www/layout-editor/gaya.css','utf8');
 const html='<!doctype html><html lang="id"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
  +'<title>Pratinjau form Metadata Artikel</title><style>'+css
  +'\nbody{padding:24px;background:#f5f3f7}#modal-isi.metadata-dialog{margin:auto;max-height:none;overflow:visible} .metadata-dialog .modal-aksi{position:static}</style>'
  +'<p style="text-align:center">Pratinjau form. Gunakan tombol Metadata Artikel pada Web UI untuk menyimpan perubahan.</p>'
  +'<main id="modal-isi" class="metadata-dialog">'+real.html.replace('<form id="metadata-form"','<form onsubmit="event.preventDefault()" id="metadata-form"')+'</main></html>';
 fs.writeFileSync(process.argv[3],html);
 console.log('Preview:',process.argv[3]);
}
