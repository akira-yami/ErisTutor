'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const base=path.resolve(__dirname,'..');
const U=require('../studio.js');
const html=fs.readFileSync(path.join(base,'index.html'),'utf8');
const portal=fs.readFileSync(path.join(base,'..','UdpListener.RichChatPortal.cs'),'utf8');
const script=fs.readFileSync(path.join(base,'chat.js'),'utf8');

test('fingerprint ổn định và không lưu toàn bộ nội dung làm key',()=>{
  const message={role:'user',date:'2026-11-06',time:'21:00',text:'Riêng tư'};
  const a=U.fingerprint(message);
  assert.equal(a,U.fingerprint({...message}));
  assert.notEqual(a,U.fingerprint({...message,text:'Thay đổi'}));
  assert.ok(!a.includes('Riêng tư'));
});
test('đính kèm text không làm vỡ fence Markdown',()=>{
  const block=U.fileBlock('readme.md','hello\n~~~~\nworld');
  assert.match(block,/~~~~~text\nhello\n~~~~\nworld\n~~~~~/);
  assert.ok(!block.includes('```html'));
});
test('giới hạn payload tính theo UTF-8 JSON, không phải số ký tự đơn thuần',()=>{
  assert.equal(U.sizeOkay('a'),true);
  assert.equal(U.sizeOkay(''),false);
  assert.equal(U.sizeOkay('a'.repeat(6001)),false);
  assert.equal(U.sizeOkay('\\'.repeat(6000)),false);
  assert.equal(U.sizeOkay('ừ'.repeat(2000)),true);
});
test('Studio assets và các điểm móc giao diện được đóng gói cùng Godot',()=>{
  for(const f of ['studio.css','studio.js']){
    assert.match(html,new RegExp(f.replace('.','\\.')));
    assert.ok(portal.includes('"'+f+'"'));
  }
  for(const id of ['messages','input','sendForm','search','dates','inspector','avatarRoom','notifyFinished','attachmentTray','fileInput','micBtn','topicList']){
    assert.match(html,new RegExp('id="'+id+'"'));
  }
});
test('SSE sử dụng cùng snapshot có nguồn gốc runtime; polling fallback vẫn tồn tại',()=>{
  assert.match(portal,/relative == "api\/events"/);
  assert.match(portal,/StreamRichChatEvents\(net\)/);
  assert.match(portal,/Volatile\.Read\(ref _richChatSnapshot\)/);
  assert.match(portal,/presence = new/);
  assert.match(script,/eris:stream-state/);
  assert.match(script,/if\(!document\.hidden&&!window\.ErisStudio\?\.isStreaming/);
});
test('keyed rendering tránh replaceChildren toàn bộ transcript khi polling',()=>{
  assert.match(script,/dataset\.renderKey/);
  assert.match(script,/root\.insertBefore\(node/);
  assert.doesNotMatch(script,/root\.replaceChildren\(frag\)/);
});

test('xóa lịch sử thực sự qua cổng Godot, có bảo vệ cùng origin và main thread',()=>{
  const historySource=fs.readFileSync(path.join(base,'..','UdpListener.ConversationUi.cs'),'utf8');
  const studio=fs.readFileSync(path.join(base,'studio.js'),'utf8');
  assert.match(portal,/relative == "api\/history\/delete"/);
  assert.match(portal,/DELETE_ERIS_HISTORY/);
  assert.match(portal,/DateTime\.TryParseExact\(date/);
  assert.match(portal,/Interlocked\.CompareExchange\(ref deletion\.Lifecycle, 1, 0\)/);
  assert.match(portal,/DeleteRichChatHistoryOnMainThread\(deletion\.Scope, deletion\.Date\)/);
  assert.match(historySource,/RemoveAll\(e => string\.Equals\(e\.Date, date, StringComparison\.Ordinal\)\)/);
  assert.match(historySource,/if \(!SaveConversationHistory\(\)\)/);
  assert.match(historySource,/ClearConversationHistory\(\)/);
  assert.match(studio,/deleteHistoryDialog/);
  assert.match(studio,/deleteWord\.value\.trim\(\)!=='XÓA'/);
  assert.match(studio,/api\/history\/delete/);
  for(const id of ['deleteAllHistoryBtn','deleteHistoryDialog','deleteHistoryConfirm','deleteHistoryCancel','deleteHistoryVerification']){
    assert.ok(html.includes('id="'+id+'"'));
  }
});

test('người dùng có thể xóa theo ngày mà không tác động Memory hay Learning',()=>{
  const studio=fs.readFileSync(path.join(base,'studio.js'),'utf8');
  assert.match(script,/dataset\.deleteDay=date/);
  assert.match(studio,/openDeletion\('day',btn\.dataset\.deleteDay\)/);
  const native=fs.readFileSync(path.join(base,'..','UdpListener.ConversationUi.cs'),'utf8');
  const fn=native.split('private string DeleteRichChatHistoryOnMainThread(')[1].split('private static void DeleteConversationStoreFile(')[0];
  assert.doesNotMatch(fn,/(?:MemoryService|LearningService|mentor\/learning|_memoryPanel)/);
  assert.match(fn,/_conversationState is "thinking"/);
});

test('formatMarkdownTranscript tạo Markdown chuẩn có ngày giờ và nội dung',()=>{
  const mdEmpty=U.formatMarkdownTranscript([]);
  assert.match(mdEmpty,/# Eris — Hội thoại/);

  const messages=[
    {role:'user',date:'2026-11-06',time:'22:00',text:'Chào Eris!'},
    {role:'assistant',date:'2026-11-06',time:'22:01',text:'Chào bạn, mình ở đây.'}
  ];
  const md=U.formatMarkdownTranscript(messages);
  assert.match(md,/# Eris Conversation Transcript/);
  assert.match(md,/### \[Bạn\] \(2026-11-06 22:00\)/);
  assert.match(md,/Chào Eris!/);
  assert.match(md,/### \[Eris\] \(2026-11-06 22:01\)/);
  assert.match(md,/Chào bạn, mình ở đây\./);
});

test('Rich Chat Settings Modal có đầy đủ các tab, hook điều khiển và phím tắt',()=>{
  const studio=fs.readFileSync(path.join(base,'studio.js'),'utf8');
  const studioCss=fs.readFileSync(path.join(base,'studio.css'),'utf8');

  // Nút mở settings trên topbar
  assert.match(html,/id="settingsBtn"/);
  assert.match(html,/id="settingsDialog"/);
  assert.match(html,/id="settingsClose"/);
  assert.match(html,/id="settingsDialogDone"/);

  // 4 tabs
  for(const tabId of ['tabBtnAppearance','tabBtnInteraction','tabBtnRuntime','tabBtnData']){
    assert.match(html,new RegExp('id="'+tabId+'"'));
  }
  for(const panelId of ['tabContentAppearance','tabContentInteraction','tabContentRuntime','tabContentData']){
    assert.match(html,new RegExp('id="'+panelId+'"'));
  }

  // Tùy chọn giao diện & phím tắt
  for(const controlId of ['themeSegmented','fontSizeSegmented','sendKeySegmented','settingShowTime','settingAutoScroll','settingSpeechInput','settingAvatarRoom','settingNotify']){
    assert.match(html,new RegExp('id="'+controlId+'"'));
  }

  // Thao tác dữ liệu: xuất Markdown & xóa dấu trang
  assert.match(html,/id="exportMarkdownBtn"/);
  assert.match(html,/id="clearBookmarksBtn"/);
  assert.match(html,/id="openDeleteFromSettingsBtn"/);

  // Phím tắt Ctrl+, và quản lý phím Enter gửi
  assert.match(studio,/event\.key===','/);
  assert.match(studio,/prefs\.sendKey==='ctrl_enter'/);
  assert.match(studio,/openSettings/);
  assert.match(studio,/closeSettings/);
  assert.match(studio,/formatMarkdownTranscript/);

  // CSS classes
  assert.match(studioCss,/\.studio-settings-dialog/);
  assert.match(studioCss,/\.settings-tab-btn/);
  assert.match(studioCss,/\.segmented-control/);
  assert.match(studioCss,/\.settings-switch/);
  assert.match(studioCss,/body\.font-sm/);
  assert.match(studioCss,/body\.font-lg/);
  assert.match(studioCss,/body\.hide-time/);
});
