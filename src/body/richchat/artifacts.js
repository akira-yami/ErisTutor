'use strict';
// P1 renderer for strictly typed Eris ArtifactEnvelope v1. No executable HTML from tools.
const ErisArtifacts = (() => {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  })[c]);
  const safeId = s => /^art_[a-f0-9]{24}$/.test(String(s ?? ''));
  const renderText = s => typeof ErisRender !== 'undefined' ? ErisRender.render(s) : `<p>${escape(s)}</p>`;
  const image = (artifact, block, index, prefix) => {
    if (!safeId(artifact.artifact_id) || !/^([a-f0-9]{64})$/i.test(block.sha256 || '')) return '';
    const path = String(block.path || '');
    if (!path || path.startsWith('/') || path.includes('..') || path.includes('\\')) return '';
    return `<figure class="artifact-image"><img loading="eager" alt="Minh họa từ công cụ Eris" src="${escape(prefix)}api/image/${artifact.artifact_id}/${index}" /></figure>`;
  };
  const blockHtml = (artifact, block, index, prefix) => {
    if (!block || typeof block !== 'object') return '';
    if (block.type === 'formula') {
      const tex = String(block.latex || '').slice(0,8000);
      return tex ? `<div class="math-block">\\[${escape(tex)}\\]</div>` : '';
    }
    if (block.type === 'image') return image(artifact,block,index,prefix);
    if (block.type === 'code') return renderText('```' + String(block.language || '').replace(/[^a-z\d+#-]/gi,'') + '\n' + String(block.text || '') + '\n```');
    if (block.type === 'table') return tableHtml(block.headers || [],block.rows || []);
    if (['text','paragraph','section','heading','quote','list'].includes(block.type))
      return renderText(String(block.text || block.title || ''));
    return '';
  };
  const tableHtml = (columns, rows) => {
    if (!Array.isArray(columns) || !Array.isArray(rows) || columns.length > 64 || rows.length > 1000) return '';
    const tr = cells => '<tr>' + cells.map(c => `<td>${escape(String(c??''))}</td>`).join('') + '</tr>';
    return '<div class="table-outer"><table><thead><tr>' + columns.map(c=>`<th>${escape(String(c??''))}</th>`).join('') +
      '</tr></thead><tbody>' + rows.filter(r=>Array.isArray(r)&&r.length===columns.length).map(tr).join('') + '</tbody></table></div>';
  };
  function render(artifact, prefix='/') {
    if (!artifact || artifact.schema!=='eris.artifact.v1' || !safeId(artifact.artifact_id)) return {html:'',error:'E_SCHEMA'};
    const kind=artifact.kind, content=artifact.content||{};
    let body='';
    if(kind==='math' && typeof content.latex==='string')
      body=`<div class="math-block">\\[${escape(content.latex)}\\]</div>`;
    else if(kind==='code' && typeof content.source==='string')
      body=renderText('```'+String(content.language||'text').replace(/[^\w+#.-]/g,'')+'\n'+content.source+'\n```');
    else if(kind==='table') body=tableHtml(content.columns,content.rows);
    else if(kind==='document') {
      const src=String(content.source||'');
      const blocks=Array.isArray(content.blocks)?content.blocks:[];
      body=src?renderText(src):blocks.slice(0,120).map((b,i)=>blockHtml(artifact,b,i,prefix)).join('');
      // Render generated image blocks even if the readable source exists.
      if(src) body+=blocks.map((b,i)=>b?.type==='image'?blockHtml(artifact,b,i,prefix):'').join('');
    } else if(kind==='chart') {
      const series=Array.isArray(content.series)?content.series[0]:null;
      const labels=series?.labels || content.labels, values=series?.values || content.values;
      if (['bar','line'].includes(content.chart_type) && Array.isArray(labels) && Array.isArray(values) && labels.length===values.length &&
          typeof ErisRender !== 'undefined')
        body=ErisRender.chartSvg(values,labels.map(String),String(content.title||''),content.chart_type);
      if (!body) return {html:'',error:'E_UNSUPPORTED_KIND'};
    } else return {html:'',error:'E_UNSUPPORTED_KIND'};
    const activity=content.activity;
    if(activity && artifact.source==='tutor_activity' &&
        /^activity:[a-f0-9]{32}$/.test(activity.activity_id||'') &&
        /^[a-f0-9]{32}$/.test(activity.nonce||'')){
      const mode=String(activity.response_mode||'choice');
      const options=Array.isArray(activity.options)?activity.options:[];
      const choice=mode==='choice' && options.length>=2 && options.length<=6;
      const typed=['numeric','reflection'].includes(mode) && options.length===0;
      if(choice||typed){
        let answerArea=choice?options.map((value,index)=>`<button type="button" class="learning-choice" data-learning-action="answer" data-learning-choice="${index}">${escape(value)}</button>`).join(''):
          `<label class="learning-answer-label" for="answer-${escape(artifact.artifact_id)}">${mode==='numeric'?'Giá trị số nguyên':'Lời giải thích của bạn (chưa tự chấm)'}</label>`+
          (mode==='numeric'?`<input id="answer-${escape(artifact.artifact_id)}" class="learning-text-answer" inputmode="numeric" maxlength="24" placeholder="Ví dụ: -3">`:
            `<textarea id="answer-${escape(artifact.artifact_id)}" class="learning-text-answer" maxlength="1200" rows="4" placeholder="Diễn đạt bằng lời của bạn..."></textarea>`)+
          '<button type="button" class="learning-submit" data-learning-action="answer">Nộp bài</button>';
        body+='<div class="learning-activity" role="group" aria-label="Bài thực hành">'+answerArea+
          '<div class="learning-help"><button type="button" data-learning-action="hint">Gợi ý thêm một bước</button>'+
          '<button type="button" data-learning-action="reveal">Xem lời giải</button></div>'+
          '<p class="learning-status" aria-live="polite">Tự thử trước, rồi nhờ Eris gợi ý khi cần.</p></div>';
      }
    }
    if (!body) return {html:'',error:'E_RENDER'};
    const title = String(artifact.presentation?.title || ({math:'Công thức',code:'Mã nguồn',table:'Bảng dữ liệu',document:'Tài liệu',chart:'Biểu đồ'}[kind]||'Artifact')).slice(0,200);
    const html = `<section class="artifact-card" data-artifact-id="${artifact.artifact_id}" data-revision="${Number(artifact.revision)||1}">`+
      `<header><span class="artifact-mark">✧</span> ${escape(title)} <small>${escape(activity?'Bài tập':kind)}</small></header>`+
      `<div class="artifact-body">${body}</div></section>`;
    return {html,error:null,hasMath:kind==='math'||body.includes('class="math-block"')||/\\\[|\\\(/.test(body)};
  }
  function flatten(bundles) {
    const result=[];
    if(!Array.isArray(bundles))return result;
    // Once a job is attached to its real chat turn, hide the provisional
    // gallery copy. Keep the immutable original sidecar for audit/replay.
    const attached=new Set();
    for(const b of bundles.slice(0,36)){
      if(!b || b.provisional || !Array.isArray(b.artifacts))continue;
      for(const a of b.artifacts){
        const ref=a?.metadata?.source_bundle_id;
        if(typeof ref==='string' && /^pi_[a-f0-9]{24}$/.test(ref)) attached.add(ref);
      }
    }
    for(const b of bundles.slice(0,36)){
      if(b?.provisional && attached.has(b.intent_id)) continue;
      if(!b || typeof b.turn_id!=='string'||!Array.isArray(b.artifacts))continue;
      for(const a of b.artifacts.slice(0,120)){
        if(a?.turn_id!==b.turn_id||!safeId(a.artifact_id)||!Number.isInteger(a.revision)||a.revision<1)continue;
        if(!Array.isArray(a.presentation?.target)||!a.presentation.target.includes('rich_chat'))continue;
        result.push({intent_id:b.intent_id,turn_id:b.turn_id,provisional:!!b.provisional,artifact:a});
      }
    }
    return result.sort((a,b)=>Number(a.artifact.presentation?.order||0)-Number(b.artifact.presentation?.order||0));
  }
  return Object.freeze({render,flatten,escape});
})();
if (typeof module!=='undefined' && module.exports) module.exports=ErisArtifacts;
