/* Eris Conversation Studio — hành vi phía trình duyệt, không tạo agent thứ hai.
   Các hành động ở đây chỉ dùng transcript/runtime thật từ cổng Godot.
   Bookmarks chỉ lưu fingerprint tin nhắn tại máy, không sao lưu nội dung riêng tư. */
'use strict';
const ErisStudioUtils = Object.freeze({
  fingerprint(message) {
    const raw=String(message?.role||'')+'\u0000'+String(message?.date||'')+'\u0000'+String(message?.time||'')+'\u0000'+String(message?.text||'');
    let h=2166136261;for(let i=0;i<raw.length;i++){h^=raw.charCodeAt(i);h=Math.imul(h,16777619);}
    return (h>>>0).toString(36);
  },
  fileBlock(name,content) {
    const clean=String(name||'tài liệu').replace(/[\r\n\[\]`]/g,' ').slice(0,95);
    // Tilde fence đủ dài để văn bản người dùng không vô tình thoát ra ngoài khối.
    const largest=Math.max(0,...Array.from(String(content).matchAll(/~{3,}/g),m=>m[0].length));
    const fence='~'.repeat(Math.max(4,largest+1));
    return `\n\nTài liệu đính kèm: ${clean}\n${fence}text\n${content}\n${fence}`;
  },
  sizeOkay(message) {
    return message.length>0 && message.length<=6000 && new TextEncoder().encode(JSON.stringify({message})).length<=11900;
  },
  formatMarkdownTranscript(messages) {
    if(!Array.isArray(messages)||!messages.length)return '# Eris — Hội thoại\n\nChưa có tin nhắn nào trong transcript.\n';
    const header=`# Eris Conversation Transcript\nXuất lúc: ${new Date().toLocaleString('vi-VN')}\nTổng số tin nhắn: ${messages.length}\n\n---\n\n`;
    const body=messages.map(m=>{
      const role=m.role==='user'?'Bạn':'Eris';
      const time=[m.date,m.time].filter(Boolean).join(' ');
      return `### [${role}] ${time?`(${time})`:''}\n\n${m.text||''}\n`;
    }).join('\n---\n\n');
    return header+body;
  }
});
if(typeof module!=='undefined'&&module.exports)module.exports=ErisStudioUtils;
if(typeof document!=='undefined')document.addEventListener('DOMContentLoaded',()=>{
  const $=id=>document.getElementById(id);
  const root=$('messages'),scroll=$('scroll'),input=$('input'),send=$('sendBtn');
  if(!root||!input||!send)return;
  const endpoint=path=>new URL(path,new URL('.',location.href)).pathname;
  const safeRead=(key,fallback)=>{try{return localStorage.getItem(key)??fallback;}catch{return fallback;}};
  const safeWrite=(key,value)=>{try{localStorage.setItem(key,value);}catch{/* storage không khả dụng */}};
  let saved=new Set();try{saved=new Set(JSON.parse(safeRead('eris.studio.bookmarks.v1','[]')));}catch{}
  let transcript=[],artifacts=[],files=[],recording=null,stream=null,streamSeen=0,streamFailures=0;
  let activeBefore=false, hadRuntimeActivity=false;
  const stageNames={idle:'Sẵn sàng',thinking:'Đang suy nghĩ',speaking:'Đang nói',planning:'Đang lên kế hoạch',staged:'Đang chuẩn bị',audio_buffering:'Đang chuẩn bị âm thanh',afterglow:'Đã hoàn tất'};
  const history=[];
  const defaultPrefs={
    theme:safeRead('eris.studio.theme','auto'),
    fontSize:safeRead('eris.studio.fontSize','base'),
    showTime:safeRead('eris.studio.showTime','1')==='1',
    sendKey:safeRead('eris.studio.sendKey','enter'),
    autoScroll:safeRead('eris.studio.autoScroll','1')==='1',
    speechInput:safeRead('eris.studio.speechInput','1')==='1',
    avatarRoom:safeRead('eris.studio.avatarRoom',innerWidth>=1570?'1':'0')==='1',
    notify:safeRead('eris.studio.notify','0')==='1'&&typeof Notification!=='undefined'&&Notification.permission==='granted'
  };
  let prefs={...defaultPrefs};
  const notification=$('notifyFinished');

  function applySettings(){
    if(prefs.theme==='dark'){document.body.classList.add('dark');}
    else if(prefs.theme==='light'){document.body.classList.remove('dark');}
    else{const prefersDark=window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches;document.body.classList.toggle('dark',prefersDark);}
    document.querySelectorAll('#themeSegmented .segmented-btn').forEach(btn=>btn.classList.toggle('active',btn.dataset.themeVal===prefs.theme));
    document.body.classList.toggle('font-sm',prefs.fontSize==='sm');
    document.body.classList.toggle('font-lg',prefs.fontSize==='lg');
    document.querySelectorAll('#fontSizeSegmented .segmented-btn').forEach(btn=>btn.classList.toggle('active',btn.dataset.sizeVal===prefs.fontSize));
    document.body.classList.toggle('hide-time',!prefs.showTime);
    if($('settingShowTime'))$('settingShowTime').checked=prefs.showTime;
    document.querySelectorAll('#sendKeySegmented .segmented-btn').forEach(btn=>btn.classList.toggle('active',btn.dataset.keyVal===prefs.sendKey));
    const hintSpan=document.querySelector('.composer-hint span:first-child');
    if(hintSpan){hintSpan.innerHTML=prefs.sendKey==='ctrl_enter'?'<kbd>Ctrl Enter</kbd> để gửi · <kbd>Enter</kbd> xuống dòng':'<kbd>Enter</kbd> để gửi · <kbd>Shift Enter</kbd> xuống dòng';}
    if($('settingAutoScroll'))$('settingAutoScroll').checked=prefs.autoScroll;
    if($('settingSpeechInput'))$('settingSpeechInput').checked=prefs.speechInput;
    if($('micBtn'))$('micBtn').style.display=prefs.speechInput?'':'none';
    if($('settingAvatarRoom'))$('settingAvatarRoom').checked=prefs.avatarRoom;
    if($('avatarRoom'))$('avatarRoom').checked=prefs.avatarRoom;
    document.body.classList.toggle('avatar-room',prefs.avatarRoom);
    if($('settingNotify'))$('settingNotify').checked=prefs.notify;
    if(notification)notification.checked=prefs.notify;
    safeWrite('eris.studio.theme',prefs.theme);
    safeWrite('eris.studio.fontSize',prefs.fontSize);
    safeWrite('eris.studio.showTime',prefs.showTime?'1':'0');
    safeWrite('eris.studio.sendKey',prefs.sendKey);
    safeWrite('eris.studio.autoScroll',prefs.autoScroll?'1':'0');
    safeWrite('eris.studio.speechInput',prefs.speechInput?'1':'0');
    safeWrite('eris.studio.avatarRoom',prefs.avatarRoom?'1':'0');
    safeWrite('eris.studio.notify',prefs.notify?'1':'0');
  }
  applySettings();
  window.matchMedia?.('(prefers-color-scheme: dark)')?.addEventListener('change',()=>{if(prefs.theme==='auto')applySettings();});

  if(notification){notification.addEventListener('change',async()=>{
    if(!notification.checked){prefs.notify=false;applySettings();return;}
    if(typeof Notification==='undefined'){notification.checked=false;prefs.notify=false;applySettings();notify('Trình duyệt không hỗ trợ thông báo.');return;}
    const perm=Notification.permission==='granted'?'granted':await Notification.requestPermission();
    prefs.notify=perm==='granted';applySettings();
    if(!prefs.notify)notify('Cần cấp quyền thông báo trong trình duyệt.');
  });}
  $('avatarRoom')?.addEventListener('change',()=>{prefs.avatarRoom=$('avatarRoom').checked;applySettings();});
  $('themeBtn')?.addEventListener('click',()=>{const isDark=document.body.classList.contains('dark');prefs.theme=isDark?'light':'dark';applySettings();});

  const settingsDialog=$('settingsDialog');
  function openSettings(tabId=null){if(!settingsDialog)return;if(tabId)switchSettingsTab(tabId);updateSettingsModal();if(!settingsDialog.open)settingsDialog.showModal();}
  function closeSettings(){if(settingsDialog?.open)settingsDialog.close();}
  $('settingsBtn')?.addEventListener('click',()=>openSettings());
  $('settingsClose')?.addEventListener('click',closeSettings);
  $('settingsDialogDone')?.addEventListener('click',closeSettings);
  settingsDialog?.addEventListener('click',event=>{if(event.target===settingsDialog)closeSettings();});

  const tabMap={tabBtnAppearance:'tabContentAppearance',tabBtnInteraction:'tabContentInteraction',tabBtnRuntime:'tabContentRuntime',tabBtnData:'tabContentData'};
  function switchSettingsTab(btnId){
    for(const [bId,pId] of Object.entries(tabMap)){
      const b=$(bId),p=$(pId),on=bId===btnId;
      b?.classList.toggle('active',on);b?.setAttribute('aria-selected',String(on));
      if(p){p.hidden=!on;p.classList.toggle('active',on);}
    }
  }
  for(const bId of Object.keys(tabMap)){$(bId)?.addEventListener('click',()=>switchSettingsTab(bId));}

  $('themeSegmented')?.addEventListener('click',event=>{const b=event.target.closest('.segmented-btn');if(b?.dataset.themeVal){prefs.theme=b.dataset.themeVal;applySettings();}});
  $('fontSizeSegmented')?.addEventListener('click',event=>{const b=event.target.closest('.segmented-btn');if(b?.dataset.sizeVal){prefs.fontSize=b.dataset.sizeVal;applySettings();}});
  $('sendKeySegmented')?.addEventListener('click',event=>{const b=event.target.closest('.segmented-btn');if(b?.dataset.keyVal){prefs.sendKey=b.dataset.keyVal;applySettings();}});
  $('settingShowTime')?.addEventListener('change',event=>{prefs.showTime=event.target.checked;applySettings();});
  $('settingAutoScroll')?.addEventListener('change',event=>{prefs.autoScroll=event.target.checked;applySettings();});
  $('settingSpeechInput')?.addEventListener('change',event=>{prefs.speechInput=event.target.checked;applySettings();});
  $('settingAvatarRoom')?.addEventListener('change',event=>{prefs.avatarRoom=event.target.checked;applySettings();});
  $('settingNotify')?.addEventListener('change',async event=>{
    if(!event.target.checked){prefs.notify=false;applySettings();return;}
    if(typeof Notification==='undefined'){event.target.checked=false;prefs.notify=false;applySettings();notify('Trình duyệt không hỗ trợ thông báo.');return;}
    const perm=Notification.permission==='granted'?'granted':await Notification.requestPermission();
    prefs.notify=perm==='granted';applySettings();
    if(!prefs.notify)notify('Cần cấp quyền thông báo trong trình duyệt.');
  });

  $('clearBookmarksBtn')?.addEventListener('click',()=>{
    if(!saved.size){notify('Chưa có dấu trang nào.');return;}
    if(!confirm(`Xóa toàn bộ ${saved.size} dấu trang đã lưu trên máy?`))return;
    saved.clear();safeWrite('eris.studio.bookmarks.v1','[]');savedNav();updateSettingsModal();
    for(const row of root.querySelectorAll('.entry')){const b=row.querySelector('[data-studio-action="bookmark"]');if(b){b.classList.remove('marked');b.textContent='✧ Lưu';}}
    notify('Đã xóa tất cả dấu trang.');
  });
  function downloadMarkdown(){
    const body=ErisStudioUtils.formatMarkdownTranscript(transcript);
    const blob=new Blob([body],{type:'text/markdown;charset=utf-8'});
    const url=URL.createObjectURL(blob);const link=document.createElement('a');link.href=url;link.download='eris-conversation-'+new Date().toISOString().slice(0,10)+'.md';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    notify('Đã xuất hội thoại sang Markdown.');
  }
  $('exportMarkdownBtn')?.addEventListener('click',downloadMarkdown);
  $('openDeleteFromSettingsBtn')?.addEventListener('click',()=>{closeSettings();openDeletion('all');});

  function updateSettingsModal(state=null){
    if($('bookmarkSummary'))$('bookmarkSummary').textContent=`${saved.size} tin nhắn đã lưu trên trình duyệt này`;
    if(!state&&window.__ERIS_LAST_STATE__)state=window.__ERIS_LAST_STATE__;
    if(!state)return;
    const runtime=state?.presence||{},speech=state?.speech||{};
    const phase=String(speech.phase||'idle'),stage=String(runtime.stage||phase);
    if($('metricStage'))$('metricStage').textContent=stageNames[stage]||stageNames[phase]||'Sẵn sàng';
    if($('metricEmotion'))$('metricEmotion').textContent=runtime.emotion||'Bình thường';
    if($('metricChannel'))$('metricChannel').textContent=stream?'SSE Live':'Polling HTTP';
  }
  const notify=text=>{const el=document.createElement('div');el.className='toast';el.textContent=text;document.body.appendChild(el);setTimeout(()=>el.remove(),2500);};
  function focusInput(value=null){if(value!==null)input.value=value;input.dispatchEvent(new Event('input'));input.focus();}
  function setView(open){document.body.classList.toggle('inspector-closed',!open);$('inspectorBtn')?.setAttribute('aria-pressed',String(open));safeWrite('eris.studio.inspector',open?'1':'0');}
  setView(safeRead('eris.studio.inspector',innerWidth>=1450?'1':'0')==='1');
  $('inspectorBtn')?.addEventListener('click',()=>setView(document.body.classList.contains('inspector-closed')));
  $('statusDetails')?.addEventListener('click',()=>setView(true));
  $('inspectorClose')?.addEventListener('click',()=>setView(false));
  function setSidebarCollapsed(collapsed){
    document.body.classList.toggle('sidebar-collapsed',collapsed);
    const label=collapsed?'Mở rộng thanh lịch sử':'Thu gọn thanh lịch sử';
    $('sidebarCollapse').title=label;
    $('sidebarCollapse').setAttribute('aria-label',label);
    safeWrite('eris.studio.sidebar',collapsed?'1':'0');
  }
  $('sidebarCollapse').addEventListener('click',()=>setSidebarCollapsed(!document.body.classList.contains('sidebar-collapsed')));
  setSidebarCollapsed(safeRead('eris.studio.sidebar','0')==='1');
  $('newTopicBtn')?.addEventListener('click',()=>{if(input.value.trim()&&!confirm('Thay nội dung nháp bằng chủ đề mới?'))return;
    focusInput('Chủ đề mới: ');$('sidebar')?.classList.remove('open');
    notify('Đây là chủ đề mới trong transcript hiện tại, không phải phiên chat tách biệt.');});
  $('openWorkspace')?.addEventListener('click',()=>{if($('workspaceBtn')?.getAttribute('aria-pressed')!=='true')$('workspaceBtn')?.click();if(innerWidth<1150)setView(false);});
  const scrollDown=()=>{scroll.scrollTo({top:scroll.scrollHeight,behavior:'smooth'});};
  $('jumpBottom')?.addEventListener('click',scrollDown);$('contextGo')?.addEventListener('click',scrollDown);
  const jumpIndicator=()=>{$('jumpBottom').hidden=scroll.scrollHeight-scroll.scrollTop-scroll.clientHeight<220;};
  scroll.addEventListener('scroll',jumpIndicator,{passive:true});
  $('promptBtn')?.addEventListener('click',()=>focusInput('Eris, cùng tôi tìm hiểu sâu về '));
  // Xóa lịch sử thật ở Godot, không chỉ ẩn tin nhắn khỏi giao diện web.
  const deleteDialog=$('deleteHistoryDialog'),deleteWord=$('deleteHistoryWord'),deleteAccept=$('deleteHistoryConfirm');
  let deletionTarget=null,deletionBusy=false;
  function openDeletion(scope,date=''){
    if(deletionBusy||!transcript.length){notify('Lịch sử đang trống hoặc đang được xử lý.');return;}
    const selection=scope==='all'?transcript:transcript.filter(m=>m.date===date);
    if(!selection.length){notify('Ngày này không có tin nhắn để xóa.');return;}
    deletionTarget={scope,date,messages:selection.map(m=>ErisStudioUtils.fingerprint(m))};
    $('deleteHistoryTitle').textContent=scope==='all'?'Xóa toàn bộ lịch sử?':'Xóa lịch sử ngày '+date+'?';
    $('deleteHistoryDescription').textContent=`${selection.length} tin nhắn sẽ bị xóa khỏi lịch sử lưu trên máy. Không thể hoàn tác.`;
    $('deleteHistoryVerification').hidden=scope!=='all';deleteWord.value='';
    deleteAccept.disabled=scope==='all';deleteAccept.textContent=scope==='all'?'Xóa tất cả':'Xóa ngày này';
    if(!deleteDialog.open)deleteDialog.showModal();
    if(scope==='all')deleteWord.focus();
  }
  $('deleteAllHistoryBtn')?.addEventListener('click',()=>openDeletion('all'));
  $('dates')?.addEventListener('click',event=>{
    const btn=event.target.closest('[data-delete-day]');if(!btn)return;
    event.preventDefault();event.stopPropagation();openDeletion('day',btn.dataset.deleteDay);
  });
  deleteWord.addEventListener('input',()=>{
    if(deletionTarget?.scope==='all'&&!deletionBusy)deleteAccept.disabled=deleteWord.value.trim()!=='XÓA';
  });
  $('deleteHistoryCancel').addEventListener('click',()=>{if(!deletionBusy)deleteDialog.close();});
  deleteDialog.addEventListener('cancel',event=>{if(deletionBusy)event.preventDefault();});
  deleteDialog.addEventListener('close',()=>{if(!deletionBusy)deletionTarget=null;});
  deleteAccept.addEventListener('click',async()=>{
    if(!deletionTarget||deletionBusy||deletionTarget.scope==='all'&&deleteWord.value.trim()!=='XÓA')return;
    const target=deletionTarget;deletionBusy=true;deleteAccept.disabled=true;
    $('deleteHistoryCancel').disabled=true;deleteAccept.textContent='Đang xóa…';
    let deleted=false;
    try{
      const response=await fetch(endpoint('api/history/delete'),{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({scope:target.scope,date:target.date,confirmation:'DELETE_ERIS_HISTORY'})});
      const data=await response.json();
      if(!response.ok||!data.ok){
        if(data.reason==='busy')notify('Eris còn đang xử lý lượt. Hãy xóa khi Eris đã trả lời xong.');
        else if(data.reason==='timeout')notify('Chưa nhận xác nhận xóa từ Eris. Kiểm tra lại lịch sử trước khi thử lần nữa.');
        else notify('Không xóa được lịch sử. Nội dung cũ vẫn được giữ.');
        return;
      }
      for(const key of target.messages)saved.delete(key);
      safeWrite('eris.studio.bookmarks.v1',JSON.stringify(Array.from(saved).slice(-180)));
      $('search').value='';$('search').dispatchEvent(new Event('input'));
      $('dates')?.querySelector('.date-button')?.click();
      deleted=true;deleteDialog.close();notify(`Đã xóa ${target.messages.length} tin nhắn khỏi lịch sử trên máy.`);
      // SSE sẽ tự cập nhật; yêu cầu snapshot ngay để fallback polling không chậm.
      try{
        const latest=await fetch(endpoint('api/state'),{cache:'no-store'});
        if(latest.ok)window.dispatchEvent(new CustomEvent('eris:stream-state',{detail:await latest.json()}));
      }catch{/* SSE / polling sẽ cập nhật; không báo xóa thất bại sau khi đã xác nhận. */}
    }catch{notify('Mất kết nối với Eris. Hãy kiểm tra lịch sử trước khi xóa lần nữa.');}
    finally{
      deletionBusy=false;
      if(deleted)deletionTarget=null;
      $('deleteHistoryCancel').disabled=false;
      deleteAccept.disabled=deleted||target.scope==='all'&&deleteWord.value.trim()!=='XÓA';
      if(!deleted)deleteAccept.textContent=target.scope==='all'?'Xóa tất cả':'Xóa ngày này';
    }
  });
  // Thêm chỉ mục lưu tin nhắn theo fingerprint, không sao chép transcript vào localStorage.
  function topicNav(){const box=$('topicList');if(!box)return;box.replaceChildren();
    const topics=transcript.filter(m=>m.role==='user'&&/^Chủ đề mới:\s*\S/i.test(m.text||'')).slice(-10).reverse();
    if(!topics.length){const p=document.createElement('p');p.className='activity-empty';p.textContent='Các chủ đề mới sẽ xuất hiện tại đây.';box.append(p);return;}
    for(const m of topics){const button=document.createElement('button');button.className='saved-button';button.type='button';
      button.textContent=m.text.replace(/^Chủ đề mới:\s*/i,'').slice(0,42);button.addEventListener('click',()=>{
        // Trở về tất cả các ngày trước khi điều hướng; không đổi transcript Eris.
        $('search').value='';$('search').dispatchEvent(new Event('input'));
        const first=$('dates')?.querySelector('.date-button');first?.click();
        setTimeout(()=>Array.from(root.querySelectorAll('.entry')).find(e=>e.dataset.msgKey===ErisStudioUtils.fingerprint(m))?.scrollIntoView({behavior:'smooth',block:'center'}),10);
        $('sidebar')?.classList.remove('open');
      });box.append(button);
    }
  }
  function savedNav(){const box=$('savedList');if(!box)return;box.replaceChildren();let count=0;
    for(const m of transcript){const key=ErisStudioUtils.fingerprint(m);if(!saved.has(key))continue;count++;
      const button=document.createElement('button');button.type='button';button.className='saved-button';button.textContent=String(m.text).replace(/\s+/g,' ').slice(0,51);
      button.title='Tới tin nhắn đã đánh dấu';button.onclick=()=>{
        const element=Array.from(root.querySelectorAll('.entry')).find(row=>row.dataset.msgKey===key);
        if(element)element.scrollIntoView({behavior:'smooth',block:'center'});
        else {const search=$('search');search.value=m.text.slice(0,65);search.dispatchEvent(new Event('input'));setTimeout(()=>root.querySelector('.entry')?.scrollIntoView({behavior:'smooth',block:'center'}),30);}
        $('sidebar')?.classList.remove('open');
      };box.append(button);
    }
    $('savedCount').textContent=String(count);
    if(count===0){const p=document.createElement('p');p.className='activity-empty';p.textContent='Chưa có tin đã lưu.';box.append(p);}
  }
  root.addEventListener('click',event=>{
    const welcome=event.target.closest('[data-welcome-prompt]');
    if(welcome){focusInput(welcome.dataset.welcomePrompt);return;}
    const btn=event.target.closest('[data-studio-action]');if(!btn)return;
    const entry=btn.closest('.entry'),key=entry?.dataset.msgKey,role=entry?.classList.contains('user')?'user':'assistant';
    const msg=transcript.find(m=>ErisStudioUtils.fingerprint(m)===key);if(!msg)return;
    const action=btn.dataset.studioAction;
    if(action==='bookmark'){
      saved.has(key)?saved.delete(key):saved.add(key);safeWrite('eris.studio.bookmarks.v1',JSON.stringify(Array.from(saved).slice(-180)));
      btn.classList.toggle('marked',saved.has(key));btn.textContent=saved.has(key)?'Đã lưu':'Lưu';savedNav();
    }else if(action==='quote'){
      const excerpt=msg.text.replace(/\s+/g,' ').slice(0,310);
      focusInput((input.value?input.value+'\n\n':'')+`> ${excerpt}\n\n`);
    }else if(action==='resend'){
      if(role!=='user')return;
      focusInput(msg.text);notify('Sửa và gửi lại sẽ tạo tin nhắn mới, không thay lịch sử cũ.');
    }else if(action==='retry'){
      if(role!=='assistant')return;
      focusInput('Eris, hãy xem lại câu trả lời vừa rồi và thử giải thích theo một hướng khác: ');
    }
  });
  // Tài liệu text được chèn vào nội dung gửi, không có dịch vụ upload giả.
  async function addFiles(list){
    for(const file of Array.from(list).slice(0,3)){
      if(files.length>=3){notify('Tối đa 3 tài liệu mỗi lần gửi.');break;}
      if(!/\.(txt|md|markdown|json|csv|xml|yaml|yml|py|js|ts|java|cs|cpp|c|h|html|css|sql|log|sh|bat)$/i.test(file.name)){
        notify('Hiện hỗ trợ tệp văn bản. Ảnh cần dùng Vision của Eris.');continue;
      }
      if(file.size>19000){notify('Tài liệu lớn quá; hãy gửi một đoạn nhỏ dưới 6.000 ký tự.');continue;}
      try{const content=await file.text();if(content.includes('\0'))throw Error('binary');
        files.push({name:file.name,text:content});renderFiles();
      }catch{notify('Không đọc được tệp '+file.name);}
    }
    updateCount();
  }
  function renderFiles(){const tray=$('attachmentTray');tray.replaceChildren();files.forEach((file,i)=>{
    const chip=document.createElement('span');chip.className='file-chip';chip.textContent='▤ '+file.name;
    const remove=document.createElement('button');remove.type='button';remove.textContent='×';remove.setAttribute('aria-label','Bỏ '+file.name);
    remove.addEventListener('click',()=>{files.splice(i,1);renderFiles();updateCount();});chip.append(remove);tray.append(chip);
  });}
  function compose(){return input.value.trim()+files.map(f=>ErisStudioUtils.fileBlock(f.name,f.text)).join('');}
  function updateCount(){const message=compose();$('composerCounter').textContent=`${message.length} / 6000`;
    $('composerCounter').classList.toggle('critical',!ErisStudioUtils.sizeOkay(message)&&message.length>0);
    send.disabled=!ErisStudioUtils.sizeOkay(message)||send.dataset.busy==='1';}
  $('attachBtn')?.addEventListener('click',()=>$('fileInput').click());
  $('fileInput')?.addEventListener('change',event=>{addFiles(event.target.files);event.target.value='';});
  const composer=$('sendForm');
  for(const type of ['dragenter','dragover'])composer.addEventListener(type,event=>{if(event.dataTransfer?.types?.includes('Files')){event.preventDefault();composer.classList.add('drop-ready');}});
  composer.addEventListener('dragleave',()=>composer.classList.remove('drop-ready'));
  composer.addEventListener('drop',event=>{event.preventDefault();composer.classList.remove('drop-ready');addFiles(event.dataTransfer.files);});
  input.addEventListener('input',updateCount);
  // Chặn submit mặc định của chat.js bằng capture nếu có tài liệu hoặc quá giới hạn.
  composer.addEventListener('submit',event=>{
    if(!files.length){if(!ErisStudioUtils.sizeOkay(input.value.trim())){event.preventDefault();event.stopImmediatePropagation();notify('Tin nhắn vượt giới hạn ký tự/byte.');}return;}
    event.preventDefault();event.stopImmediatePropagation();
    const message=compose();if(!ErisStudioUtils.sizeOkay(message)){notify('Tệp + câu hỏi quá dài. Rút gọn tài liệu trước khi gửi.');return;}
    if(send.dataset.busy==='1')return;
    send.dataset.busy='1';updateCount();
    fetch(endpoint('api/send'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message})})
      .then(resp=>{if(!resp.ok)throw Error('send failed');files=[];renderFiles();input.value='';input.dispatchEvent(new Event('input'));$('draftStatus').textContent='Đã gửi tài liệu vào hội thoại';})
      .catch(()=>notify('Không gửi được tài liệu. Kiểm tra kết nối Eris.'))
      .finally(()=>{delete send.dataset.busy;updateCount();});
  },true);
  updateCount();
  // Nhập giọng nói do browser cung cấp; TTS Eris vẫn nằm ở Godot.
  const Recognizer=window.SpeechRecognition||window.webkitSpeechRecognition;
  if(!Recognizer){$('micBtn').title='Trình duyệt chưa hỗ trợ SpeechRecognition';}
  $('micBtn')?.addEventListener('click',()=>{
    if(!Recognizer){notify('Trình duyệt này chưa hỗ trợ nhập bằng giọng nói.');return;}
    if(recording){recording.stop();return;}
    const recognition=new Recognizer();recording=recognition;recognition.lang='vi-VN';recognition.continuous=false;recognition.interimResults=false;
    $('micBtn').classList.add('recording');$('micBtn').setAttribute('aria-label','Dừng nghe');
    recognition.onresult=event=>{const text=Array.from(event.results).map(r=>r[0]?.transcript||'').join(' ').trim();if(text)focusInput(input.value+(input.value?' ':'')+text);};
    recognition.onerror=()=>notify('Không nhận được âm thanh. Kiểm tra quyền micro.');
    recognition.onend=()=>{recording=null;$('micBtn').classList.remove('recording');$('micBtn').setAttribute('aria-label','Nhập bằng giọng nói');};
    try{recognition.start();}catch{recognition.onend();notify('Không thể mở micro.');}
  });
  input.addEventListener('keydown',event=>{
    if(event.key==='Enter'&&!event.isComposing){
      if(prefs.sendKey==='ctrl_enter'){
        if(event.ctrlKey||event.metaKey){event.preventDefault();composer.requestSubmit();}
        else{event.stopImmediatePropagation();}
      }
    }
  },true);
  document.addEventListener('keydown',event=>{
    if((event.ctrlKey||event.metaKey)&&event.key===','){event.preventDefault();openSettings();}
    const searchShortcut=(event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='k';
    const slashShortcut=event.key==='/'&&!['INPUT','TEXTAREA'].includes(document.activeElement?.tagName);
    if(searchShortcut||slashShortcut){
      event.preventDefault();
      setSidebarCollapsed(false);
      $('sidebar').classList.add('open');
      $('search').focus();
    }
    if(event.key==='Escape'){if(recording)recording.stop();if(settingsDialog?.open){closeSettings();return;}setView(false);$('sidebar').classList.remove('open');}
  });
  function activityUpdate(state){
    const runtime=state?.presence||{},speech=state?.speech||{};
    const phase=String(speech.phase||'idle'),stage=String(runtime.stage||phase);
    const label=String(runtime.activity||runtime.label||state.status||stageNames[stage]||stageNames[phase]).slice(0,180);
    const currentlyActive=phase==='thinking'||phase==='speaking'||!!runtime.isActive;
    // Chỉ thông báo sau khi đã THẤY một lượt làm việc thực sự trong tab này.
    // Không thông báo lần đầu nạp history, không thông báo khi mất kết nối.
    if(activeBefore&&!currentlyActive&&hadRuntimeActivity&&document.hidden&&notification?.checked){
      try{new Notification('Eris đã hoàn tất lượt',{body:'Bạn có thể mở lại hội thoại để xem kết quả.'});}catch{/* Tắt thông báo nếu browser từ chối. */}
    }
    activeBefore=currentlyActive;if(currentlyActive)hadRuntimeActivity=true;
    $('presenceLabel').textContent=label;
    $('stageTag').textContent=stageNames[stage]||stageNames[phase]||'Hoạt động';
    $('presenceOrb').className='presence-orb'+(phase==='thinking'?' thinking':phase==='speaking'?' speaking':'');
    $('contextText').textContent=label;
    const emotion=String(runtime.emotion||'');$('emotionTag').hidden=!emotion;if(emotion)$('emotionTag').textContent=emotion.slice(0,35);
    const key=[stage,label,runtime.activity||''].join('|');
    if(label && history[0]?.key!==key){history.unshift({key,label,stage,time:new Date().toLocaleTimeString('vi-VN',{hour:'2-digit',minute:'2-digit'})});history.splice(12);renderHistory();}
    const count=artifacts.length;$('artifactCount').textContent=count?`${count} kết quả trong hội thoại.`:'Chưa có tài liệu trong hội thoại.';
    updateSettingsModal(state);
  }
  function renderHistory(){const box=$('activityTimeline');box.replaceChildren();for(const item of history){
    const div=document.createElement('div');div.className='timeline-item';const content=document.createElement('div');content.textContent=item.label;
    const time=document.createElement('small');time.textContent=item.time+' · '+(stageNames[item.stage]||item.stage);content.append(time);div.append(content);box.append(div);
  }}
  window.addEventListener('eris:state',event=>{
    const state=event.detail;if(!state)return;
    const prevCount=transcript.length;
    transcript=Array.isArray(state.messages)?state.messages:[];
    artifacts=Array.isArray(state.bundles)?state.bundles.flatMap(b=>b.artifacts||[]):[];
    $('messageCount').textContent=transcript.length+' tin nhắn';
    activityUpdate(state);topicNav();savedNav();jumpIndicator();
    if(prefs.autoScroll&&transcript.length>prevCount)scrollDown();
    for(const row of root.querySelectorAll('.entry')){
      const btn=row.querySelector('[data-studio-action="bookmark"]');if(!btn)continue;
      const marked=saved.has(row.dataset.msgKey);btn.classList.toggle('marked',marked);btn.textContent=marked?'Đã lưu':'Lưu';
    }
  });
  // EventSource phát toàn bộ snapshot thật (KHÔNG có stream token nếu runtime chưa phát token).
  // Nếu stream hỏng, chat.js tự quay về polling cũ.
  function startStream(){if(typeof EventSource==='undefined'||window.__ERIS_FIXTURE__)return;
    stream=new EventSource(endpoint('api/events'));
    stream.addEventListener('state',event=>{try{const state=JSON.parse(event.data);streamSeen=Date.now();streamFailures=0;
      window.dispatchEvent(new CustomEvent('eris:stream-state',{detail:state}));}catch(error){console.warn('[Eris] SSE invalid state',error);}});
    stream.addEventListener('error',()=>{streamFailures++;if(streamFailures>3){stream.close();stream=null;}});
  }
  window.ErisStudio=Object.freeze({isStreaming:()=>!!stream&&stream.readyState===1&&Date.now()-streamSeen<4000,
    saved:()=>saved,compose,updateCount,openSettings,closeSettings,getPrefs:()=>({...prefs}),applySettings,downloadMarkdown});
  startStream();
  // chat.js có thể nhận snapshot fixture ngay trước khi studio.js đăng ký listener.
  if(window.__ERIS_LAST_STATE__)window.dispatchEvent(new CustomEvent('eris:state',{detail:window.__ERIS_LAST_STATE__}));
  // Xuất transcript cục bộ khi người dùng tự chọn (không gửi lên dịch vụ ngoài).
  const exportBtn=document.createElement('button');exportBtn.type='button';exportBtn.className='saved-button';exportBtn.textContent='↓ Xuất hội thoại (.md)';
  exportBtn.addEventListener('click',downloadMarkdown);
  $('sidebar').querySelector('.sidebar-foot').prepend(exportBtn);
});
