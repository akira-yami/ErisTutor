'use strict';
// The browser is a presentation/client layer. All focus, grading and mastery
// decisions belong to runtime.learning_canvas in the Python backend.
(() => {
  const byId = id => document.getElementById(id);
  const systemTheme = matchMedia('(prefers-color-scheme: dark)');
  function syncTheme() {
    let theme = 'auto';
    try {theme = localStorage.getItem('eris.studio.theme') || 'auto';} catch (_) {}
    document.body.classList.toggle('dark', theme === 'dark' || (theme === 'auto' && systemTheme.matches));
  }
  syncTheme();
  systemTheme.addEventListener('change', syncTheme);
  window.addEventListener('storage', syncTheme);
  byId('library').open = matchMedia('(min-width: 861px)').matches;
  const state = {
    snapshot: null, selected: '', mode: 'map', pending: null, desiredNode: '',
    details: new Map(), activities: new Map(), drafts: new Map(), answers: new Map(),
    graphDrafts: new Map(), showAllChildren: false, lessonRevealed: new Set(),
    playback: null, playbackSeq: -1, playbackStream: '',
    lastVersion: -1, lastPoll: 0, lastExercisePoll: 0, lastCause: '',
    busy: false, bootstrapped: false, status: 'Đang kết nối với Eris...', offline: false,
    lastActivityAt: Date.now(), lastHeartbeatAt: Date.now(),
  };
  // P3.7: định danh từng tab, không dùng cookie/localStorage để nhận diện user.
  const clientId = 'tab-' + (globalThis.crypto?.randomUUID?.() ||
    `${Date.now()}-${Math.random().toString(36).slice(2)}`);
  function sessionSignal(action) {
    if (!state.bootstrapped) return;
    // Độc lập với state.pending để có thể close ngay cả khi user đang submit.
    fetch('api/canvas/request', {method:'POST', keepalive:true,
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({action,client_id:clientId,request_id:requestId()}),
      cache:'no-store'}).catch(()=>{});
  }
  const labels = {available: 'Có thể học', learning: 'Đang học', practicing: 'Đang luyện', mastered: 'Đã vững'};
  const continuityLabels = {exploring:'Đang tìm hiểu',viewed:'Đã xem',practiced:'Đã thực hành',mastered:'Đã chứng minh thành thạo'};
  const jobLabels = {deferred:'Chờ bạn mở môn học',queued:'Gemini đang chuẩn bị',ready:'Bản đồ đã sẵn sàng',unavailable:'Gemini chưa khả dụng',superseded:'Đã bỏ yêu cầu cũ',unchanged:'Đã có nhánh',interrupted:'Gián đoạn khi khởi động lại'};
  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }
  function workspace() {return state.snapshot?.graph_workspace;}
  function graph() {return workspace()?.graph || null;}
  function graphMode() {return !!graph();}
  function activeNodes() {
    if (!graph()) return [];
    // P3.5: chỉ vẽ đường đã đi và tối đa 4 lựa chọn ngay trước mắt.
    // Các tầng khác tồn tại trên backend, nhưng không phủ kín màn hình.
    const nodes = graph().nodes || [];
    const byKey = new Map(nodes.map(n => [n.id,n]));
    const chosen = byKey.has(state.selected) ? state.selected : graph().selected_node_id;
    const ancestors = [], visited = new Set();
    let cursor = chosen;
    while (byKey.has(cursor) && !visited.has(cursor)) {
      visited.add(cursor);ancestors.unshift(byKey.get(cursor));
      cursor = byKey.get(cursor).parent_id;
    }
    const children = nodes.filter(n => n.parent_id === chosen);
    const visible = ancestors.concat(state.showAllChildren ? children : children.slice(0,4));
    return visible.map((n,i) => ({...n, level:ancestors.includes(n)
      ? ancestors.indexOf(n) : ancestors.length,
      state:'exploring', prerequisites:n.parent_id ? [n.parent_id] : []}));
  }
  function nodeById(id) {return activeNodes().find(n => n.id === id);}
  function learningProgress(groupId,nodeId) {
    return state.snapshot?.learning_continuity?.nodes?.[groupId+'/'+nodeId] || null;
  }
  function currentActivity() {return state.activities.get(state.selected);}
  function setStatus(value) {state.status = value; byId('status').textContent = value;}
  function errMessage(reason) {
    const known = {
      stale_canvas_revision:'Bản đồ đã được Eris cập nhật. Hãy chọn lại kiến thức.',
      canvas_focus_changed:'Eris vừa đổi trọng tâm học. Vui lòng thử lại.',
      canvas_exercise_running:'Bài Java vẫn đang chạy.',
      canvas_submission_budget_exceeded:'Bạn đã dùng hết lượt nộp bài này.',
      stale_graph_revision:'Đồ thị đã đổi ở cửa sổ khác. Hãy thử thao tác lại.',
      graph_group_limit:'Đã đạt giới hạn số bản đồ trong P3.',
      graph_node_limit:'Nhánh này đã đạt giới hạn nút của bản P3.',
      graph_payload_limit:'Nội dung bản đồ đã quá lớn cho một lượt đồng bộ.',
      duplicate_graph_child:'Đã có nhánh trùng tên trong cùng một nút cha.'
    };
    return known[reason] || reason || 'Không thể đồng bộ. Hãy thử lại.';
  }
  function requestId() {
    const uuid = globalThis.crypto?.randomUUID?.();
    return 'web-' + (uuid || `${Date.now()}-${Math.random().toString(36).slice(2)}`);
  }
  async function send(action, fields = {}) {
    if (state.pending || (!state.snapshot && action !== 'open')) return false;
    const request_id = requestId();
    const packet = {request_id, action, ...fields};
    if (action === 'open') packet.client_id = clientId;
    if (action !== 'open') packet.base_revision = state.snapshot.focus.revision;
    if (action.startsWith('graph_')) packet.base_graph_revision = workspace()?.revision ?? 0;
    state.pending = {request_id, action, fields, started: Date.now()};
    setStatus('Đang đồng bộ với Eris...');
    renderButtons();
    try {
      const response = await fetch('api/canvas/request', {method:'POST',
        headers:{'Content-Type':'application/json'}, body:JSON.stringify(packet), cache:'no-store'});
      if (!response.ok) throw new Error('Yêu cầu bị từ chối (' + response.status + ')');
      return true;
    } catch (error) {
      state.pending = null;
      setStatus('Không gửi được yêu cầu: ' + error.message);
      renderButtons();
      return false;
    }
  }
  function mergeActivity(activity) {
    if (!activity || !activity.node_id || !activity.activity_id) return;
    const prev = state.activities.get(activity.node_id);
    if (prev) {
      if ((activity.created_at || 0) < (prev.created_at || 0)) return;
      if (activity.activity_id === prev.activity_id &&
          (activity.submitted_at || 0) < (prev.submitted_at || 0)) return;
      if (activity.activity_id === prev.activity_id && activity.submission_id &&
          activity.submission_id === prev.submission_id && prev.status !== 'running' && activity.status === 'running') return;
    }
    state.activities.set(activity.node_id, activity);
    if ((activity.type === 'java' || activity.type === 'python') && !state.drafts.has(activity.activity_id))
      state.drafts.set(activity.activity_id, activity.code || '');
  }
  function observePlayback(packet) {
    if (!packet || !Number.isSafeInteger(packet.seq)) return;
    if (packet.stream_id && state.playbackStream && packet.stream_id !== state.playbackStream) return;
    if (packet.seq <= state.playbackSeq) return;
    state.playbackSeq = packet.seq;
    state.playback = packet.final ? null : packet;
  }
  function ingest(payload) {
    if (!payload || typeof payload !== 'object') return;
    if (payload.version === state.lastVersion) return;
    state.lastVersion = payload.version;
    const snapshot = payload.learning_canvas;
    const restarted = !!(snapshot?.playback_stream_id && state.playbackStream &&
      snapshot.playback_stream_id !== state.playbackStream);
    if (snapshot?.schema === 'eris.learning.canvas.v1' && Array.isArray(snapshot.nodes) && snapshot.focus) {
      const previous = state.snapshot;
      const previousGraphRevision = previous?.graph_workspace?.revision ?? -1;
      const nextGraphRevision = snapshot?.graph_workspace?.revision ?? -1;
      if (restarted || !previous || snapshot.focus.revision > previous.focus.revision ||
          (snapshot.focus.revision === previous.focus.revision && nextGraphRevision >= previousGraphRevision)) {
        if (restarted) {state.details.clear();state.activities.clear();state.lastCause = '';}
        state.snapshot = snapshot;
        if (snapshot.detail?.id) state.details.set(snapshot.detail.id, snapshot.detail);
        mergeActivity(snapshot.activity);
        if (snapshot.playback_stream_id && snapshot.playback_stream_id !== state.playbackStream) {
          state.playbackStream = snapshot.playback_stream_id;
          state.playbackSeq = -1;
          state.playback = null;
        }
        observePlayback(snapshot.playback);
        if (graph()) {
          state.selected = graph()?.selected_node_id || state.selected;
        } else {
          state.selected = '';
        }
        if (previous?.focus.node_id !== snapshot.focus.node_id && !state.pending)
          state.mode = 'detail';
        const cause = snapshot.focus.cause_id;
        if (cause && cause !== snapshot.focus.acknowledged_cause_id && cause !== state.lastCause && !state.pending && !state.desiredNode) {
          state.lastCause = cause;
          // This is merely an acknowledgment; never navigate on an Eris-initiated focus change.
          queueMicrotask(() => send('ack_eris_focus', {cause_id:cause}));
        }
      }
    }
    mergeActivity(payload.exercise_update);
    observePlayback(payload.playback);
    if (state.pending) {
      const ack = (payload.acks || []).find(a => a.request_id === state.pending.request_id);
      if (ack) {
        const {action,fields} = state.pending;
        state.pending = null;
        if (ack.status === 'ok') {
          if (action === 'graph_expand') state.graphDrafts.delete(fields.parent_id);
          setStatus(action === 'mark_viewed' ? 'Đã ghi lịch sử xem · không tính mastery' :
            action.startsWith('graph_') ? 'Đã lưu bản đồ khám phá · chưa chấm mastery' : 'Đã đồng bộ với Eris');
        } else {
          setStatus(errMessage(ack.message || ack.status));
          if (action === 'navigate') state.selected = state.snapshot?.focus?.node_id || state.selected;
        }
      }
    }
    // A click during the initial Open ACK must not be lost when that older
    // snapshot arrives. Apply the most recent desired navigation afterward.
    if (state.desiredNode && !state.pending) {
      const desired = state.desiredNode;
      state.desiredNode = '';
      queueMicrotask(() => chooseNode(desired));
    }
    render();
  }
  async function poll() {
    if (state.busy) return;
    state.busy = true;
    try {
      const ctrl = new AbortController();
      const timeout = setTimeout(() => ctrl.abort(), 3500);
      let response;
      try {response = await fetch('api/canvas/state', {cache:'no-store', signal:ctrl.signal});}
      finally {clearTimeout(timeout);}
      if (!response.ok) throw new Error('HTTP ' + response.status);
      const data = await response.json();
      state.offline = false;
      ingest(data);
      if (state.pending && Date.now() - state.pending.started > 9500) {
        state.pending = null;
        setStatus('Chưa nhận được xác nhận từ backend. Nhấn Làm mới để thử lại.');
        renderButtons();
        if (state.desiredNode) {
          const desired = state.desiredNode;
          state.desiredNode = '';
          queueMicrotask(() => chooseNode(desired));
        }
      }
      if (!state.bootstrapped && !state.pending && !document.hidden) {state.bootstrapped = true; send('open');}
      const activity = currentActivity();
      if (!state.pending && activity?.status === 'running' && Date.now() - state.lastExercisePoll > 1600) {
        state.lastExercisePoll = Date.now();
        send('exercise_status', {activity_id:activity.activity_id, nonce:activity.nonce});
      }
    } catch (_) {
      state.offline = true;
      setStatus('Mất kết nối ERIS. Mở ứng dụng và thử lại.');
    } finally {state.busy = false;}
  }
  function renderButtons() {
    byId('refresh').disabled = !!state.pending;
    byId('topicSelect').disabled = !!state.pending;
    byId('newTopicTitle').disabled = !!state.pending;
    byId('newTopicButton').disabled = !!state.pending;
    document.querySelectorAll('[data-action]').forEach(node => {node.disabled = !!state.pending || node.dataset.disabled === 'true';});
  }
  let navDebounce = null;
  function chooseNode(id) {
    if (!nodeById(id)) return;
    state.selected = id;
    state.mode = 'detail';
    state.showAllChildren = false;
    if (state.pending) {
      state.desiredNode = id;
      render();
      return;
    }
    state.desiredNode = '';
    render();
    if (navDebounce) clearTimeout(navDebounce);
    navDebounce = setTimeout(() => {
      navDebounce = null;
      if (graph()) {
        if (id !== graph()?.selected_node_id)
          send('graph_select',{group_id:graph().group_id,node_id:id});
        return;
      }
    }, 120);
  }
  function renderMap() {
    const map = byId('map');
    map.replaceChildren();
    const nodes = activeNodes();
    byId('nodeCount').textContent = nodes.length ? `${nodes.length} kiến thức` : '—';
    if (!nodes.length) {map.append(el('div','placeholder','Chưa có bản đồ. Nhấn "+ Tạo bản đồ" ở góc trái để bắt đầu chủ đề mới.')); return;}
    const levels = [...new Set(nodes.map(n => n.level || 0))].sort((a,b) => a-b);
    for (const level of levels) {
      const group = el('section','map-layer');
      group.append(el('span','level-head',`TẦNG ${level + 1}`));
      const row = el('div','level-row');
      for (const n of nodes.filter(n => (n.level || 0) === level)) {
        const selected = n.id === state.selected;
        const btn = el('button','node' + (selected ? ' selected' : '') +
          (state.playback?.node_id === n.id ? ' speaking-node' : '') +
          (n.due ? ' due-node' : ''));
        btn.type = 'button';btn.dataset.node = n.id;btn.setAttribute('aria-pressed',String(selected));
        btn.append(el('span','node-top',n.title));
        const foot = el('div','node-foot');
        const progress = graphMode() && learningProgress(graph().group_id,n.id);
        foot.append(el('span','node-state '+(graphMode() ? 'exploring' : (labels[n.state] ? n.state:'available')),
          graphMode() ? (progress?.status === 'viewed' ? '✓ Đã xem · chưa kiểm chứng' :
            n.origin === 'gemini_draft' ? 'Gemini · nháp' : n.origin === 'eris_proposed' ? 'Eris đề xuất' : 'Đang khám phá')
            : (labels[n.state] || 'Có thể học')));
        if (graphMode() && graph()?.prepared_counts?.[n.id]) foot.append(el('span','node-hint','Đã chuẩn bị tầng sau'));
        if (n.due) foot.append(el('span','node-hint','Đến hạn ôn'));
        else if (state.snapshot.focus?.suggested_node_id === n.id) foot.append(el('span','node-hint','Eris gợi ý ↗'));
        else if (n.result_count) foot.append(el('span','node-count',`${n.result_count} kết quả`));
        btn.append(foot);btn.addEventListener('click',() => chooseNode(n.id));row.append(btn);
      }
      group.append(row);map.append(group);
    }
    if (graphMode()) {
      const count = (graph().nodes || []).filter(n => n.parent_id === state.selected).length;
      if (count > 4 && !state.showAllChildren) {
        const more = el('button','graph-more',`+ Xem thêm ${count - 4} nhánh`);
        more.type = 'button';more.addEventListener('click',()=>{state.showAllChildren=true;render();});
        map.append(more);
      }
    }
    requestAnimationFrame(drawConnections);
  }
  function drawConnections() {
    const svg = byId('links'), map = byId('map'), viewport = byId('mapViewport');
    svg.replaceChildren();
    if (!state.snapshot) return;
    // Overlay is positioned in the same scroll content coordinates as the rows.
    const view = viewport.getBoundingClientRect();
    const rects = new Map([...map.querySelectorAll('[data-node]')].map(button => [button.dataset.node,button.getBoundingClientRect()]));
    if (!viewport.clientWidth) return;
    const width = Math.max(map.offsetWidth + 2 * map.offsetLeft, viewport.clientWidth);
    const height = Math.max(map.offsetHeight + 2 * map.offsetTop, viewport.clientHeight);
    svg.setAttribute('viewBox',`0 0 ${width} ${height}`);
    svg.style.width = width + 'px'; svg.style.height = height + 'px';
    const position = (rect, bottom) => ({x:rect.left - view.left + viewport.scrollLeft + rect.width/2,
      y:(bottom ? rect.bottom : rect.top) - view.top + viewport.scrollTop});
    const ns = 'http://www.w3.org/2000/svg';
    for (const n of activeNodes()) {
      const dest = rects.get(n.id);if (!dest) continue;
      for (const id of n.prerequisites || []) {
        const source = rects.get(id);if (!source) continue;
        const a = position(source,true),b = position(dest,false);
        const line = document.createElementNS(ns,'path');
        const mid = (a.y + b.y) / 2;
        line.setAttribute('d',`M ${a.x} ${a.y} C ${a.x} ${mid} ${b.x} ${mid} ${b.x} ${b.y}`);
        svg.append(line);
      }
    }
  }
  function addText(parent, tag, cls, text) {const element=el(tag,cls,text);parent.append(element);return element;}
  function actionButton(parent, label, action, disabled=false, primary=false) {
    const button = el('button','button'+(primary?'':' secondary'),label);
    button.type = 'button';button.dataset.action = action;button.dataset.disabled = String(disabled);
    button.disabled = disabled || !!state.pending;
    parent.append(button);return button;
  }
  function renderActivity(container, detail) {
    addText(container,'h4','section-title','Luyện tập');
    const choices = el('div','exercise-choices');container.append(choices);
    for (const exercise of detail.exercises || []) {
      const button = actionButton(choices,exercise.type === 'java'?'⌘ Viết Java':exercise.type === 'python'?'⌘ Viết Python':'◉ Trắc nghiệm','exercise');
      button.addEventListener('click',()=>send('exercise',{node_id:state.selected,exercise_id:exercise.id}));
    }
    const activity = currentActivity();
    if (!activity) {addText(container,'p','summary','Chọn dạng bài để bắt đầu. Kết quả được lưu sau khi chấm.');return;}
    const card = el('section','exercise-card');container.append(card);
    if (activity.unavailable_notice) addText(card,'p','warning',(activity.type === 'python'?'Python':'Java')+' chưa khả dụng; đã chuyển sang trắc nghiệm. '+activity.unavailable_notice);
    addText(card,'p','',activity.prompt || '');
    const running = activity.status === 'running';
    let answer = '';
    if (activity.type === 'java' || activity.type === 'python') {
      const isPy = activity.type === 'python';
      const editor = el('textarea','code-editor');editor.id = 'codeEditor';
      editor.setAttribute('aria-label',isPy?'Soạn mã Python':'Soạn mã Java');editor.spellcheck = false;
      editor.value = state.drafts.get(activity.activity_id) ?? activity.code ?? '';
      editor.disabled = running || !!state.pending;
      editor.addEventListener('input',()=>state.drafts.set(activity.activity_id,editor.value));
      editor.addEventListener('keydown',event=>{
        if (event.key === 'Tab') {
          event.preventDefault();const start=editor.selectionStart,end=editor.selectionEnd;
          editor.setRangeText('    ',start,end,'end');state.drafts.set(activity.activity_id,editor.value);
        }
      });
      card.append(editor);
      const row = el('div','exercise-actions');card.append(row);
      const submitLabel = running?'Đang chạy trong Docker...':(isPy?'▶ Chạy Python':'▶ Chạy Java');
      const submit = actionButton(row,submitLabel,'submit',running,true);
      submit.addEventListener('click',()=>{
        const code = editor.value;
        if (!code.trim() || new TextEncoder().encode(code).length > 12000) {
          setStatus('Nhập code '+(isPy?'Python':'Java')+' tối đa 12 KB.');return;
        }
        send('submit',{activity_id:activity.activity_id,nonce:activity.nonce,code});
      });
    } else {
      const list = el('div','choice-list');card.append(list);
      const selected = state.answers.get(activity.activity_id) || '';
      for (const opt of activity.options || []) {
        const button = el('button','choice'+(selected === opt.id?' selected':''),`${String(opt.id).toUpperCase()}. ${opt.text}`);
        button.type = 'button';button.setAttribute('aria-pressed',String(selected === opt.id));
        button.disabled = running || !!state.pending;
        button.addEventListener('click',()=>{state.answers.set(activity.activity_id,opt.id);renderDetail();});
        list.append(button);
      }
      const row = el('div','exercise-actions');card.append(row);
      const submit = actionButton(row,'Nộp đáp án','submit',running || !selected,true);
      submit.addEventListener('click',()=>send('submit',{activity_id:activity.activity_id,nonce:activity.nonce,answer:state.answers.get(activity.activity_id)||''}));
    }
    const actions = el('div','exercise-actions');card.append(actions);
    actionButton(actions,'Gợi ý','hint',running).addEventListener('click',()=>send('hint',{activity_id:activity.activity_id,nonce:activity.nonce}));
    actionButton(actions,'Xem lời giải','reveal',running).addEventListener('click',()=>send('reveal',{activity_id:activity.activity_id,nonce:activity.nonce}));
    addText(card,'p','summary',activity.assistance === 'hinted'?'Đã dùng gợi ý · Kết quả ghi kèm trợ giúp':
      activity.assistance === 'revealed'?'Đã xem lời giải · Kết quả ghi kèm trợ giúp':'Đang tự làm');
    if (activity.help) addText(card,'div','help',activity.help);
    if (activity.status === 'completed') {
      addText(card,'p','result '+(activity.result === 'correct'?'correct':'incorrect'),
        activity.result === 'correct'?'✓ Đúng · Đã lưu kết quả':activity.result === 'partial'?'Đúng một phần · Đã lưu kết quả':'Chưa đúng · Đã lưu kết quả');
      if (activity.tests?.length) {
        const tests=el('ul','tests');card.append(tests);
        for (const test of activity.tests) addText(tests,'li','',`${test.passed?'✓':'×'} ${test.description}`);
      }
      if (activity.diagnostic) addText(card,'p','help',activity.diagnostic);
    }
    if (activity.status === 'unavailable') addText(card,'p','warning','Không thể chạy '+(activity.type === 'python'?'Python':'Java')+'. '+(activity.guidance || 'Kiểm tra Docker Desktop và môi trường container.'));
  }
  // P4: MathJax tải từ bundle cục bộ và chỉ khi gặp block công thức.
  // Không nhúng HTML từ Gemini; công thức cũng được đặt bằng textContent.
  let mathLoading = null;
  function typesetLesson(host) {
    if (globalThis.MathJax?.typesetPromise) {
      MathJax.typesetPromise([host]).catch(()=>{});
      return;
    }
    if (!mathLoading) {
      mathLoading = new Promise(resolve => {
        globalThis.MathJax = {tex:{inlineMath:[['\\(','\\)']],displayMath:[['\\[','\\]']]},svg:{fontCache:'global'}};
        const script = document.createElement('script');
        script.src = 'vendor/mathjax.js';
        script.onload = () => Promise.resolve(globalThis.MathJax?.startup?.promise).then(resolve,resolve);
        script.onerror = resolve;
        document.head.append(script);
      });
    }
    mathLoading.then(()=>{
      if (host.isConnected && globalThis.MathJax?.typesetPromise)
        MathJax.typesetPromise([host]).catch(()=>{});
    });
  }
  function renderDynamicLesson(container, node) {
    const packet = state.snapshot?.dynamic_lesson || {};
    const status = packet.status || 'idle';
    const section = el('section','dynamic-lesson');container.append(section);
    const heading = el('div','dynamic-lesson-heading');section.append(heading);
    addText(heading,'h4','','Bài học theo nhánh');
    addText(heading,'span','lesson-draft-label','Bản nháp · không chấm mastery');
    if (packet.content?.group_id === graph()?.group_id && packet.content?.node_id === node.id && status === 'ready') {
      const narrateBtn = actionButton(heading,'🎙 Eris giảng bài này','lesson_narrate');
      narrateBtn.addEventListener('click',()=>send('lesson_narrate',{group_id:graph()?.group_id || packet.content?.group_id,node_id:node.id}));
      let hasMath = false;
      const blocks = packet.content.blocks || [];
      for (let bi = 0; bi < blocks.length; bi++) {
        const block = blocks[bi];
        const blockId = block.id || ('b-' + bi);
        const isSpeaking = state.playback?.node_id === node.id &&
          (state.playback?.block_id === blockId || state.playback?.block_id === ('b-' + bi)) &&
          state.playback?.event_type === 'audio_start';
        const card = el('section','lesson-block lesson-'+block.kind+(isSpeaking?' speaking-block':''));
        card.dataset.blockId = blockId;
        section.append(card);
        addText(card,'h5','lesson-block-title',block.title);
        if (block.kind === 'text') addText(card,'p','lesson-copy',block.text);
        else if (block.kind === 'steps') {
          const list=el('ol','lesson-steps');card.append(list);
          for (const step of block.items || []) addText(list,'li','',step);
        } else if (block.kind === 'formula') {
          hasMath=true;
          addText(card,'div','lesson-formula','\\['+block.latex+'\\]');
          if (block.explanation) addText(card,'p','lesson-copy',block.explanation);
        } else if (block.kind === 'code') {
          const wrapper=el('div','code-wrap');
          const pre=el('pre','language-'+block.language);
          const code=el('code','language-'+block.language,block.text);
          pre.append(code);wrapper.append(pre);card.append(wrapper);
          if (globalThis.Prism?.highlightElement) globalThis.Prism.highlightElement(code);
        } else if (block.kind === 'check') {
          addText(card,'p','lesson-copy',block.question);
          const key=packet.content.group_id+'/'+packet.content.node_id+'/'+String((packet.content.blocks||[]).indexOf(block));
          let options = block.options;
          if (!Array.isArray(options) || !options.length) {
            const lines = (block.question || '').split('\n').map(l => l.trim()).filter(Boolean);
            const optLines = lines.filter(l => /^[A-D][\.\:\)]\s+/i.test(l));
            if (optLines.length >= 2) options = optLines;
          }
          const isRevealed = state.lessonRevealed.has(key);
          if (Array.isArray(options) && options.length >= 2) {
            const list = el('div','choice-list');card.append(list);
            const userPick = state.answers.get(key) || '';
            const checkAnswer = (pick) => {
              const ans = (block.answer || '').trim().toLowerCase();
              const p = String(pick || '').trim().toLowerCase();
              const ansLetter = ans.match(/^([a-d])[\.\:\)]?/i)?.[1]?.toLowerCase() || ans;
              const pickLetter = p.match(/^([a-d])[\.\:\)]?/i)?.[1]?.toLowerCase() || p;
              if (ansLetter && pickLetter && ansLetter === pickLetter) return true;
              return p === ans || (ans.length > 2 && p.includes(ans)) || (p.length > 2 && ans.includes(p));
            };
            for (const opt of options) {
              const optId = typeof opt === 'object' ? opt.id : opt;
              const optLabel = typeof opt === 'object' ? `${String(opt.id).toUpperCase()}. ${opt.text}` : opt;
              const isSelected = userPick === optId;
              let choiceClass = 'choice' + (isSelected ? ' selected' : '');
              if (isSelected) {
                choiceClass += checkAnswer(optId) ? ' correct' : ' incorrect';
              }
              const btn = el('button', choiceClass, optLabel);
              btn.type = 'button';
              btn.addEventListener('click', () => {
                state.answers.set(key, optId);
                renderDetail();
              });
              list.append(btn);
            }
            if (userPick) {
              const isCorrect = checkAnswer(userPick);
              const feedback = el('div', 'result ' + (isCorrect ? 'correct' : 'incorrect'));
              feedback.textContent = isCorrect ? '✓ Chính xác!' : '✗ Chưa đúng rồi. Thử chọn lại nhé!';
              if (isCorrect && block.explanation) {
                const exp = el('p', 'lesson-caption', block.explanation);
                feedback.append(exp);
              }
              card.append(feedback);
            }
            const actions = el('div','exercise-actions');card.append(actions);
            const show = actionButton(actions, isRevealed ? 'Ẩn giải thích' : 'Xem giải thích', 'lesson_reveal');
            show.addEventListener('click', () => {
              if (isRevealed) state.lessonRevealed.delete(key); else state.lessonRevealed.add(key);
              renderDetail();
            });
            if (isRevealed) {
              addText(card, 'p', 'lesson-answer', 'Đáp án: ' + block.answer + (block.explanation ? (' — ' + block.explanation) : ''));
            }
          } else {
            const show = actionButton(card, isRevealed ? 'Ẩn đáp án' : 'Xem lời giải', 'lesson_reveal');
            show.addEventListener('click', () => {
              if (isRevealed) state.lessonRevealed.delete(key); else state.lessonRevealed.add(key);
              renderDetail();
            });
            if (isRevealed) addText(card, 'p', 'lesson-answer', block.answer + (block.explanation ? (' — ' + block.explanation) : ''));
          }
          addText(card,'p','lesson-caption','Tự kiểm tra · thực hành trực tiếp giúp ghi nhớ sâu.');
        }
      }
      if (hasMath) typesetLesson(section);
    } else if (packet.node_id === node.id && status === 'preparing') {
      addText(section,'p','lesson-loading','✦ Gemini đang chuẩn bị nội dung ngắn cho đúng nhánh này...');
    } else {
      addText(section,'p','lesson-loading',status === 'unavailable' ?
        'Chưa tạo được bài học. Bạn có thể xem nhánh hoặc thử lại.' :
        'Chưa có bài học lưu sẵn. Khi mở nhánh, Eris sẽ thử tạo nội dung.');
      const button=actionButton(section,status==='unavailable'?'Thử tạo lại':'Tạo bài học','lesson_request',
        !!state.pending || node.id !== graph()?.selected_node_id);
      button.addEventListener('click',()=>send('lesson_request',{
        group_id:graph().group_id,node_id:node.id}));
    }
  }

  function renderDetail() {
    const container = byId('detail');
    // Preserve unsent Java drafts and cursor while new playback packets arrive.
    const oldEditor = byId('codeEditor');
    const focused = document.activeElement === oldEditor;
    const selection = focused ? [oldEditor.selectionStart,oldEditor.selectionEnd] : null;
    // P3: nếu backend cập nhật giữa lúc người dùng đang gõ, giữ vị trí con trỏ.
    const graphInput = document.activeElement?.matches?.('.branch-input,.branch-summary')
      ? document.activeElement : null;
    const graphField = graphInput?.classList.contains('branch-input') ? 'title' : 'summary';
    const graphSelection = graphInput ? [graphInput.selectionStart,graphInput.selectionEnd] : null;
    if (oldEditor && currentActivity()) state.drafts.set(currentActivity().activity_id,oldEditor.value);
    container.replaceChildren();
    const node = nodeById(state.selected);
    byId('focusState').textContent = node ? 'Nhánh khám phá' : '—';
    if (!node) {addText(container,'div','placeholder','Chưa có nội dung. Chọn một mục trên bản đồ hoặc tạo chủ đề mới để bắt đầu.');return;}
    const head = el('div','detail-head');container.append(head);
    addText(head,'span','eyebrow','KIẾN THỨC / '+(node.level + 1));
    addText(head,'h3','',node.title);
    if (graphMode()) {
      addText(head,'p','summary',node.summary || 'Chủ đề đang khám phá. Chưa có bài giảng đã kiểm chứng.');
      const progress = learningProgress(graph().group_id,node.id);
      const minutes = Math.floor((progress?.estimated_seconds || 0) / 60);
      addText(head,'p','graph-ai-status','Lịch sử: '+(continuityLabels[progress?.status] || 'Chưa ghi nhận')+
        (minutes ? ` · khoảng ${minutes} phút tương tác` : '')+
        ' · Mở xem không có nghĩa đã thành thạo.');
      if (progress?.status !== 'viewed') {
        const viewed = actionButton(head,'✓ Tôi đã xem phần này','mark_viewed',
          node.id !== graph()?.selected_node_id);
        viewed.addEventListener('click',()=>send('mark_viewed',{group_id:graph().group_id,node_id:node.id}));
      }
      addText(head,'p','draft-warning',node.origin === 'gemini_draft'
        ? 'Nhánh nháp Gemini · chưa kiểm chứng, không tự động tính mastery.'
        : node.origin === 'eris_proposed' ? 'Nhánh do Eris đề xuất · cần kiểm chứng trước khi đánh giá trình độ.'
        : 'Nhánh khám phá · không tự động tính mastery.');
      // P4: bài học chỉ thuộc nhánh người học đang chọn; không render cache ẩn.
      renderDynamicLesson(container,node);
      const parent = node.parent_id && nodeById(node.parent_id);
      if (parent) {
        const back = actionButton(head,'← '+parent.title,'graph_select');
        back.addEventListener('click',()=>chooseNode(parent.id));
      }
      const children = activeNodes().filter(n=>n.parent_id===node.id);
      const graphStatus = state.snapshot?.graph_prefetch;
      if (graphStatus?.node_id === node.id && graphStatus.status === 'preparing')
        addText(container,'p','graph-ai-status','✧ Gemini đang chuẩn bị một vài nhánh tiếp theo ở nền...');
      else if (graphStatus?.node_id === node.id && graphStatus.status === 'unavailable')
        addText(container,'p','graph-ai-status','Gemini chưa phản hồi. Bạn vẫn có thể tự mở nhánh hoặc hỏi Eris.');
      const hidden = graph()?.prepared_counts?.[node.id] || 0;
      if (hidden) addText(container,'p','graph-ai-status',`Có ${hidden} gợi ý ẩn đã chuẩn bị; mở nhánh để xem.`);
      if (children.length) {
        addText(container,'h4','section-title','Có thể khám phá tiếp');
        const childRow = el('div','graph-children');container.append(childRow);
        for (const child of children) {
          const childButton = actionButton(childRow,child.title,'graph_select');
          childButton.addEventListener('click',()=>chooseNode(child.id));
        }
      }
      const allChildrenCount = (graph().nodes || []).filter(n=>n.parent_id===node.id).length;
      if (allChildrenCount > children.length) {
        const show = actionButton(container,`Xem thêm ${allChildrenCount - children.length} nhánh`,'show_more');
        show.addEventListener('click',()=>{state.showAllChildren=true;render();});
      }
      const form = el('form','branch-form');
      form.append(addText(form,'label','section-title','Mở rộng kiến thức từ nút này'));
      const topicInput = el('input','branch-input');topicInput.maxLength=120;
      topicInput.placeholder='Tên nhánh con...';topicInput.required=true;
      topicInput.setAttribute('aria-label','Tên nhánh kiến thức');form.append(topicInput);
      const saved = state.graphDrafts.get(node.id) || {title:'',summary:''};
      topicInput.value = saved.title;
      topicInput.addEventListener('input',()=>state.graphDrafts.set(node.id,
        {title:topicInput.value,summary:summaryInput.value}));
      const summaryInput = el('textarea','branch-summary');summaryInput.maxLength=240;summaryInput.rows=3;
      summaryInput.placeholder='Ghi chú hoặc câu hỏi (không bắt buộc)...';
      summaryInput.setAttribute('aria-label','Ghi chú nhánh kiến thức');form.append(summaryInput);
      summaryInput.value = saved.summary;
      summaryInput.addEventListener('input',()=>state.graphDrafts.set(node.id,
        {title:topicInput.value,summary:summaryInput.value}));
      const submit = actionButton(form,'+ Thêm nhánh','graph_expand',false,true);submit.type='submit';
      form.addEventListener('submit',e=>{e.preventDefault();if (!topicInput.value.trim()) return;
        send('graph_expand',{group_id:graph().group_id,parent_id:node.id,
          title:topicInput.value.trim(),summary:summaryInput.value.trim()});
      });
      container.append(form);
      if (graphInput) {
        const target = graphField === 'title' ? topicInput : summaryInput;
        target.focus();target.setSelectionRange(...graphSelection);
      }
      renderButtons();return;
    }
    if (node.prerequisite_warnings?.length) {
      addText(head,'div','warning','Nên ôn trước: '+node.prerequisite_warnings.map(id=>nodeById(id)?.title||id).join(', ')+'. Bạn vẫn có thể học phần này.');
    }
    const detail = state.details.get(node.id);
    if (!detail) {addText(container,'div','placeholder','Đang tải nội dung từ Eris...');return;}
    addText(head,'p','summary',detail.summary || '');
    for (const block of detail.blocks || []) {
      const active = state.playback?.node_id === node.id && state.playback?.block_id === block.id &&
        state.playback.event_type === 'audio_start';
      const card = el('section','block'+(active?' active':''));
      card.dataset.block = block.id;
      addText(card,'h4','block-title',block.title);
      if (block.kind === 'java') {
        const wrapper=el('div','code-wrap'),pre=el('pre','language-java'),code=el('code','language-java',block.text);
        pre.append(code);wrapper.append(pre);card.append(wrapper);
        if (globalThis.Prism?.highlightElement) globalThis.Prism.highlightElement(code);
      } else addText(card,'p','block-text',block.text);
      container.append(card);
    }
    for (const result of state.snapshot?.late_results || []) addText(container,'p','late','Kết quả đã có · '+(result.summary || ''));
    renderActivity(container,detail);
    renderButtons();
    if (focused && byId('codeEditor')) {
      const editor = byId('codeEditor');editor.focus();editor.setSelectionRange(...selection);
    }
  }
  function renderSpeaking() {
    const playback=state.playback, node=nodeById(playback?.node_id);
    const visible = !!node && !!playback && playback.node_id !== state.selected &&
      ['queued','audio_start'].includes(playback.event_type);
    byId('speaking').hidden = !visible;
    if (visible) byId('speakingText').textContent =
      (playback.event_type==='audio_start'?'Eris đang nói về ':'Eris chuẩn bị nói về ')+node.title;
    byId('speakingJump').onclick = () => chooseNode(playback.node_id);
  }
  function renderContinuity() {
    const fsrs = byId('fsrsReviewQueue');
    if (fsrs) {
      const due = state.snapshot?.due_review || [];
      const mode = state.snapshot?.retention?.active_mode || 'fsrs';
      if (due.length > 0) {
        fsrs.hidden = false;
        fsrs.replaceChildren();
        fsrs.append(el('span','review-tag',`Ôn tập (${mode}) · ${due.length} đến hạn`));
        for (const item of due.slice(0, 6)) {
          const title = nodeById(item.kp_id)?.title || item.kp_id.split(':').pop() || item.kp_id;
          const pill = el('button','review-pill',title);
          pill.type = 'button';
          pill.title = `Chuyển đến ôn tập: ${title}`;
          pill.addEventListener('click',()=>chooseNode(item.kp_id));
          fsrs.append(pill);
        }
      } else {
        fsrs.hidden = true;
      }
    }
    const c=state.snapshot?.learning_continuity || {};
    const subjects=byId('recentSubjects'),history=byId('learningHistory'),jobs=byId('learningJobs');
    subjects.replaceChildren();history.replaceChildren();jobs.replaceChildren();
    for (const item of (c.recent_subjects || []).slice(0,4)) {
      const btn=el('button','continue-card');btn.type='button';btn.disabled=!!state.pending;
      btn.append(el('strong','',item.title||item.group_id));
      btn.append(el('span','',`↳ ${item.last_node_title || 'Chủ đề chính'} · Tiếp tục học`));
      btn.addEventListener('click',()=>send('graph_select',{group_id:item.group_id}));
      subjects.append(btn);
    }
    if (!subjects.children.length) subjects.append(el('span','history-empty','Môn học đã mở sẽ xuất hiện ở đây.'));
    for (const item of (c.events || []).slice(0,8)) {
      const summary=item.action?.startsWith('status_')?'Đánh dấu '+(continuityLabels[item.action.slice(7)]||item.action):
        item.action==='created'?'Tạo bản đồ':item.action==='resumed'?'Học tiếp':'Mở kiến thức';
      history.append(el('p','history-entry',`${summary}: ${item.title||''} · ${new Date((item.at||0)*1000).toLocaleString('vi-VN')}`));
    }
    if (!history.children.length) history.append(el('p','history-empty','Chưa có hoạt động.'));
    for (const job of (c.jobs || []).slice(0,5)) {
      jobs.append(el('p','history-entry',`${job.title||'Bản đồ'} · ${jobLabels[job.status]||job.status}`));
    }
    if (!jobs.children.length) jobs.append(el('p','history-empty','Chưa có tác vụ.'));
  }
  function render() {
    const snap=state.snapshot;
    const currentGraph = graph();
    byId('pathTitle').textContent = currentGraph ? currentGraph.title : 'Chưa chọn chủ đề';
    byId('workspaceType').textContent = currentGraph ? 'Bản đồ chủ đề' : 'Không gian học';
    byId('learningLegend').classList.toggle('hidden-by-mode', !!currentGraph);
    const groupSelect = byId('topicSelect');
    const items = workspace()?.groups || [];
    const signature = items.map(item => item.group_id+'|'+item.title).join(';');
    if (groupSelect.dataset.signature !== signature) {
      if (!items.length) {
        groupSelect.replaceChildren(el('option','','Chưa có chủ đề'));
      } else {
        groupSelect.replaceChildren(...items.map(item=>{const opt=el('option','',item.title);opt.value=item.group_id;return opt;}));
      }
      groupSelect.dataset.signature=signature;
    }
    groupSelect.value = workspace()?.selected_group_id || (items.length ? items[0].group_id : '');
    byId('pin').textContent = currentGraph ? 'Chế độ khám phá' : 'Theo Eris';
    byId('assessment').textContent = currentGraph ? 'Khám phá từng nhánh theo thứ tự bạn chọn' : 'Chọn hoặc tạo chủ đề để bắt đầu';
    const mobile=state.mode==='detail';
    byId('workspace').classList.toggle('detail-mode', mobile);
    byId('mapTab').classList.toggle('active',!mobile);
    byId('detailTab').classList.toggle('active',mobile);
    byId('mapTab').setAttribute('aria-pressed',String(!mobile));
    byId('detailTab').setAttribute('aria-pressed',String(mobile));
    renderMap();renderDetail();renderSpeaking();renderContinuity();renderButtons();
  }
  byId('refresh').addEventListener('click',()=>send('open'));
  byId('topicSelect').addEventListener('change',event=>{
    if (!state.snapshot) return;
    send('graph_select',{group_id:event.target.value});
  });
  byId('newTopicForm').addEventListener('submit',event=>{
    event.preventDefault();
    const input=byId('newTopicTitle');
    if (!input.value.trim()) return;
    send('graph_create',{title:input.value.trim()});
    input.value='';
  });
  byId('mapTab').addEventListener('click',()=>{state.mode='map';render();});
  byId('detailTab').addEventListener('click',()=>{state.mode='detail';render();});
  window.addEventListener('resize',()=>requestAnimationFrame(drawConnections));
  byId('mapViewport').addEventListener('scroll',()=>requestAnimationFrame(drawConnections),{passive:true});
  // Chỉ ước lượng thời gian học khi tab còn tương tác; không biến thành mastery.
  ['pointerdown','keydown','wheel','touchstart'].forEach(type=>
    document.addEventListener(type,()=>{state.lastActivityAt=Date.now();},{passive:true}));
  setInterval(()=>{
    const now=Date.now();
    if (document.hidden || state.pending || !state.snapshot || state.offline ||
        now-state.lastActivityAt>90000 || now-state.lastHeartbeatAt<30000) return;
    const group_id=workspace()?.selected_group_id;
    const node_id=graph()?graph()?.selected_node_id:'';
    if (!group_id || !node_id) return;
    state.lastHeartbeatAt=now;
    send('study_heartbeat',{group_id,node_id});
  },30000);
  render();
  poll();
  // Không polling cả graph khi tab đã bị ẩn. Khi trở lại, gia hạn phiên trước
  // rồi mới đọc snapshot; panel đóng quá 40 giây sẽ tự hết lease ở backend.
  document.addEventListener('visibilitychange',()=>{
    if (document.hidden) sessionSignal('session_close');
    else {sessionSignal('session_ping'); if (!state.pending && state.snapshot) send('open'); poll();}
  });
  window.addEventListener('pagehide',()=>sessionSignal('session_close'));
  setInterval(()=>{if (!document.hidden && !state.offline) sessionSignal('session_ping');},12000);
  setInterval(()=>{if (!document.hidden) poll();},850);
})();
