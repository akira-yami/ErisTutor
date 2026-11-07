'use strict';
// ERIS Learner Portrait: display-only. No scoring, no curriculum mutation, no Gemini requests from the browser.
(() => {
  const $ = id => document.getElementById(id);
  const el = (tag, cls = '', text = '') => { const v = document.createElement(tag); if (cls) v.className = cls; if (text) v.textContent = text; return v; };
  const NS = 'http://www.w3.org/2000/svg';
  const svgEl = (tag, attrs = {}) => { const n = document.createElementNS(NS, tag); for (const [k,v] of Object.entries(attrs)) n.setAttribute(k,String(v)); return n; };
  const STATUS = { review_due: 'Đến hạn ôn', needs_repair: 'Nên giải thích lại', possible_repeated_gap: 'Có dấu hiệu lỗi lặp', needs_independent_probe: 'Cần thử tự làm', unassessed: 'Chưa đánh giá', evidence_available: 'Đã có bằng chứng' };
  const viewNames = { portrait: 'CHÂN DUNG', knowledge:'KIẾN THỨC', evidence:'BẰNG CHỨNG', growth:'PHÁT TRIỂN' };
  let data = null, current = 'portrait', lastPayload = '', generationPollAt = 0, nodePicked = '';
  const setConnection = (online, label) => { $('connectionDot').classList.toggle('online', online); $('connectionText').textContent = label; };
  const setView = view => {
    if (!(view in viewNames)) return;
    current = view; $('crumb').textContent = viewNames[view];
    for (const button of document.querySelectorAll('[data-view]')) {
      const yes = button.dataset.view === view; button.classList.toggle('active', yes);
      button.setAttribute('aria-current', yes ? 'page' : 'false');
    }
    for (const name of Object.keys(viewNames)) $('view-' + name).hidden = name !== view;
    if (data && view === 'knowledge') renderGraph();
  };
  function clear(node) { node.replaceChildren(); }
  function text(parent, tag, cls, value) { const n = el(tag,cls,String(value ?? '')); parent.append(n); return n; }
  function label(signal) { return STATUS[signal] || 'Cần quan sát'; }
  function tone(node) {
    if (node.independent_correct > 0 || node.independent_incorrect > 0) return 'green';
    if (node.signal && node.signal !== 'unassessed') return 'amber';
    return 'grey';
  }
  function renderPortrait() {
    $('summaryText').textContent = data.summary || 'Chưa đủ dữ liệu để nhận định.';
    const model = data.gemini || {}, observations = $('geminiObservations'); clear(observations);
    const state = model.state || 'empty';
    $('geminiCaption').textContent = state === 'ready' ? 'Gemini • diễn giải từ nguồn' : state === 'generating' ? 'Gemini đang tổng hợp nguồn…' : 'Mô tả từ Learning Core';
    for (const item of (model.observations || [])) {
      if (!item || !item.text || !(item.source_refs || []).length) continue;
      const card = el('div','observation'); card.textContent = item.text;
      card.title = 'Tham chiếu: ' + item.source_refs.join(', '); observations.append(card);
    }
    const cards = $('spaceCards'); clear(cards);
    for (const [index, path] of (data.spaces || []).slice(0,8).entries()) {
      const b = el('button','space-card'); b.type = 'button'; b.title = 'Xem bản đồ: ' + path.title;
      text(b,'div','space-icon',['✳','⌘','◈','✧','◇','✦','⬡','◌'][index%8]);
      text(b,'h3','',path.title);
      text(b,'p','',path.current_kp_title || path.goal || 'Chưa có vị trí học đang hoạt động.');
      const foot = text(b,'div','space-foot',''); text(foot,'span','',`${path.kp_count} điểm kiến thức`); text(foot,'span','', '↗');
      b.addEventListener('click',async () => { setView('knowledge'); if(path.path_id !== data.selection.path_id) await ask('select',path.path_id); });
      cards.append(b);
    }
    const more = el('button','space-card add'); more.type = 'button';
    text(more,'div','space-icon','＋'); text(more,'h3','','Xem tất cả lộ trình'); text(more,'p','','Không cần định nghĩa sẵn các môn học.');
    more.addEventListener('click', () => setView('knowledge')); cards.append(more);
    const caps = $('capabilityCards'); clear(caps);
    if (!(data.capabilities || []).length) text(caps,'p','muted','Chưa có thông tin năng lực kỹ thuật từ Mentor.');
    for (const cap of data.capabilities || []) {
      const card=el('div','cap'); const side=el('div');
      text(side,'strong','',cap.title); text(side,'small','',cap.observed ? `${cap.evidence_count} dấu vết · Độ tin cậy ${cap.confidence}` : `${cap.evidence_count} dấu vết · Chưa đủ bằng chứng`);
      card.append(side); text(card,'span','tag' + (cap.observed ? ' known' : ''),cap.observed ? 'Có quan sát' : 'Chưa rõ'); caps.append(card);
    }
  }
  function renderPaths() {
    const select = $('pathSelect'), selected = data.selection?.path_id || '';
    clear(select);
    for (const p of data.spaces || []) { const o = document.createElement('option'); o.value=p.path_id; o.textContent=p.title; o.selected=p.path_id===selected; select.append(o); }
    if (!select.options.length) { const o = document.createElement('option'); o.textContent='Chưa có lộ trình'; select.append(o); }
    $('mapCount').textContent = `${(data.knowledge || []).length} / ${data.selection?.kp_count || 0} nút`;
    $('pageCount').textContent = `${(data.spaces_page || 0)+1} / ${data.spaces_pages || 1}`;
    $('pagePrev').disabled = (data.spaces_page || 0) <= 0;
    $('pageNext').disabled = (data.spaces_page || 0) >= (data.spaces_pages || 1)-1;
    $('truncatedNotice').hidden = !(data.knowledge_truncated || data.spaces_truncated);
  }
  function layoutNodes(nodes) {
    const lookup = new Map(nodes.map(n => [n.id,n])); const memo = new Map();
    function depth(id, parents=new Set()) {
      if (memo.has(id)) return memo.get(id);
      if (parents.has(id)) return 0;
      const item=lookup.get(id); if(!item) return 0;
      const next=new Set(parents); next.add(id);
      let d=0; for(const prereq of item.prerequisites || []) if (lookup.has(prereq)) d=Math.max(d,Math.min(6,depth(prereq,next)+1));
      memo.set(id,d); return d;
    }
    const columns=new Map();
    for (const n of nodes) { const d=depth(n.id); if(!columns.has(d)) columns.set(d,[]); columns.get(d).push(n); }
    const maxRows=Math.max(1,...[...columns.values()].map(a=>a.length)), positions=new Map();
    for (const [d,items] of columns) items.forEach((n,i) => positions.set(n.id,{x:25+d*235,y:44+i*90+(maxRows-items.length)*45}));
    return {positions,width:Math.max(510,Math.max(...columns.keys(),0)*235+240),height:Math.max(380, maxRows*90+70)};
  }
  function renderGraph() {
    const host=$('graphHost'); clear(host); const nodes=data?.knowledge || [];
    if(!nodes.length){ text(host,'p','muted',data?.spaces?.length ? 'Lộ trình này chưa có điểm kiến thức để hiển thị.' : 'Chưa có lộ trình học nào được lưu.'); renderDetail(null);return; }
    const {positions,width,height}=layoutNodes(nodes);
    const graph=svgEl('svg',{width,height,viewBox:`0 0 ${width} ${height}`,role:'img','aria-label':'Bản đồ kiến thức theo các quan hệ tiên quyết'});
    const defs=svgEl('defs'); const marker=svgEl('marker',{id:'edgeArrow',viewBox:'0 0 10 10',refX:9,refY:5,markerWidth:5,markerHeight:5,orient:'auto-start-reverse'});
    marker.append(svgEl('path',{d:'M 0 0 L 10 5 L 0 10 z',fill:'#d9cad9'})); defs.append(marker);graph.append(defs);
    for (const node of nodes) {
      const end=positions.get(node.id);
      for(const parent of node.prerequisites || []) {
        const start=positions.get(parent); if(!start) continue;
        const x1=start.x+180,y1=start.y+27,x2=end.x,y2=end.y+27;
        graph.append(svgEl('path',{d:`M ${x1} ${y1} C ${x1+30} ${y1}, ${x2-30} ${y2}, ${x2} ${y2}`,fill:'none',stroke:'#d9cad9','stroke-width':1.6,'marker-end':'url(#edgeArrow)'}));
      }
    }
    for (const node of nodes) {
      const {x,y}=positions.get(node.id),kind=tone(node);
      const group=svgEl('g',{class:'graph-node',tabindex:0,role:'button','aria-label':node.title,transform:`translate(${x},${y})`});
      const fill=kind==='green'?'#eaf4ee':kind==='amber'?'#fcf3e7':'#f7f3f8',stroke=kind==='green'?'#9bbca9':kind==='amber'?'#dfc7a3':'#dfd5e3';
      group.append(svgEl('rect',{class:'node-shape',x:0,y:0,rx:12,width:180,height:56,fill,stroke,'stroke-width':1.3}));
      group.append(svgEl('circle',{cx:14,cy:28,r:4,fill:kind==='green'?'#79a28a':kind==='amber'?'#d0a773':'#b1aabc'}));
      const parts=Array.from(node.title || 'Kiến thức');
      const line=parts.length>24?parts.slice(0,23).join('')+'…':node.title;
      const title=svgEl('text',{x:25,y:25,class:'graph-label'}); title.textContent=line; group.append(title);
      const sub=svgEl('text',{x:25,y:42,class:'graph-sub'}); sub.textContent=label(node.signal);group.append(sub);
      const open=()=>{nodePicked=node.id;renderDetail(node);};
      group.addEventListener('click',open);group.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();open();}});
      graph.append(group);
    }
    host.append(graph); renderDetail(nodes.find(n=>n.id===nodePicked)||nodes[0]);
  }
  function renderDetail(node) {
    const dest=$('nodeDetail'); clear(dest);
    text(dest,'span','detail-eyebrow','CHI TIẾT KIẾN THỨC');
    if (!node) {text(dest,'h3','','Chưa có nút');text(dest,'p','','Bản đồ sẽ xuất hiện sau khi Learning Core tạo lộ trình học.');return;}
    text(dest,'h3','',node.title);text(dest,'p','',label(node.signal));
    const rows=[['Tự làm đúng',node.independent_correct],['Tự làm sai',node.independent_incorrect],['Có hỗ trợ',node.assisted_families],['Ôn tập',node.review_due ? 'Đến hạn' : 'Chưa đến hạn']];
    for (const [name,value] of rows){const row=el('div','detail-line');text(row,'strong','',name);text(row,'span','',value);dest.append(row);}
    text(dest,'p','','Chỉ phản ánh bài thực hành được ghi nhận. Không phải xếp hạng năng lực.');
  }
  function renderEvidence() {
    const panel=$('evidenceRows');clear(panel);const nodes=data?.knowledge || [];
    if(!nodes.length) {text(panel,'p','muted','Chưa có bằng chứng học tập ở lộ trình được chọn.');return;}
    for(const [i,node] of nodes.entries()) {
      const row=el('div','evidence-item');text(row,'div','number',String(i+1).padStart(2,'0'));
      const body=el('div','text');text(body,'strong','',node.title);
      text(body,'small','',`Tự làm: ${node.independent_correct} đúng / ${node.independent_incorrect} sai · Có trợ giúp: ${node.assisted_families}`);
      row.append(body);text(row,'span','grade',label(node.signal));panel.append(row);
    }
  }
  function renderGrowth() {
    const panel=$('growthStats');clear(panel);const n=data.knowledge || [];
    const stats=[['Lộ trình đã lưu',data.spaces_total || 0],['Điểm kiến thức đang xem',n.length],['Kiến thức có bài độc lập',n.filter(x=>x.independent_correct+x.independent_incorrect>0).length],['Cần kiểm tra độc lập',n.filter(x=>x.signal==='needs_independent_probe').length],['Đến hạn ôn',n.filter(x=>x.review_due).length]];
    for(const [label,value] of stats){const card=el('div','stat');text(card,'strong','',value);text(card,'span','',label);panel.append(card);}
  }
  function display(payload) {
    if(!payload || payload.schema!=='eris.learner_portrait.v1') return;
    data=payload; $('loading').hidden=true; $('error').hidden=true; $('content').hidden=false;
    renderPortrait();renderPaths();renderEvidence();renderGrowth();if(current==='knowledge')renderGraph();
    const truncated=data.spaces_truncated ? ' · Danh sách rút gọn' : '';
    $('syncState').textContent='Đã đồng bộ'+truncated;
  }
  async function ask(action='refresh',pathId='') {
    try {
      const url=action==='select'?'../api/portrait/select':action==='page'?'../api/portrait/page':'../api/portrait/refresh';
      const body=action==='select'?{path_id:pathId}:action==='page'?{page:Number(pathId)}:{};
      const res=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      if(!res.ok) throw Error('Yêu cầu bị từ chối');
      $('syncState').textContent='Đang cập nhật…';
    } catch(e){$('syncState').textContent=e.message||'Không gửi được';}
  }
  async function poll() {
    try {
      const res=await fetch('../api/portrait/state',{cache:'no-store'});if(!res.ok)throw Error('offline');
      const state=await res.json();setConnection(true,'Đã kết nối');
      if(state.status==='error'){const err=$('error');err.textContent=state.message||'Có lỗi đọc hồ sơ.';err.hidden=false;$('loading').hidden=true;return;}
      if(state.data?.schema==='eris.learner_portrait.v1'){
        const raw=JSON.stringify(state.data);
        if(raw!==lastPayload){lastPayload=raw;display(state.data);}
        if(state.data.gemini?.state==='generating' && Date.now()-generationPollAt>5300){
          generationPollAt=Date.now();await ask('refresh');
        }
      }
    } catch(e){setConnection(false,'Mất kết nối với Eris');$('syncState').textContent='Ngoại tuyến';}
  }
  document.querySelectorAll('[data-view]').forEach(btn=>btn.addEventListener('click',()=>setView(btn.dataset.view)));
  $('exploreAll').addEventListener('click',()=>setView('knowledge'));
  $('refresh').addEventListener('click',()=>ask('refresh'));
  $('pagePrev').addEventListener('click',()=>ask('page',Math.max(0,(data?.spaces_page||0)-1)));
  $('pageNext').addEventListener('click',()=>ask('page',Math.min((data?.spaces_pages||1)-1,(data?.spaces_page||0)+1)));
  $('mapReset').addEventListener('click',()=>{const host=$('graphHost'); host.scrollTop=0;host.scrollLeft=0;});
  $('pathSelect').addEventListener('change',e=>{nodePicked='';ask('select',e.target.value);});
  poll();setInterval(poll,1800);
})();
