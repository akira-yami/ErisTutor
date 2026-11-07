const {test}=require('node:test');
const assert=require('node:assert/strict');
const renderer=require('../../body/richchat/artifacts.js');
const activity={activity_id:'activity:'+ 'a'.repeat(32),nonce:'b'.repeat(32),revision:'c'.repeat(64),options:['4','6','10']};
function artifact(){return {schema:'eris.artifact.v1',artifact_id:'art_'+ 'd'.repeat(24),turn_id:'turn:1',revision:1,
 source:'tutor_activity',kind:'document',content:{format:'markdown',source:'Question',activity},presentation:{title:'Python'}};}
test('trusted learning activity has answer and help buttons without answer key',()=>{
 const result=renderer.render(artifact());
 assert.equal(result.error,null);
 assert.equal((result.html.match(/class="learning-choice"/g)||[]).length,3);
 assert.match(result.html,/Gợi ý thêm một bước/);
 assert.match(result.html,/Bài tập/);
 assert.doesNotMatch(result.html,/correct_index|expected_answer/);
});
test('untrusted presentation cannot create learning controls',()=>{
 const item=artifact();item.source='root_presentation';
 assert.doesNotMatch(renderer.render(item).html,/data-learning-action/);
});
test('choice text is escaped and never executable HTML',()=>{
 const item=artifact();item.content={...item.content,activity:{...activity,options:['<img src=x onerror=alert(1)>','safe']}};
 const html=renderer.render(item).html;
 assert.doesNotMatch(html,/<img src=x/);
 assert.match(html,/&lt;img/);
});
