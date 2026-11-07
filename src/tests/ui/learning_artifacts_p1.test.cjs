const {test}=require('node:test');
const assert=require('node:assert/strict');
const renderer=require('../../body/richchat/artifacts.js');
const base={activity_id:'activity:'+'a'.repeat(32),nonce:'b'.repeat(32),revision:'c'.repeat(64),options:[]};
function artifact(mode){return {schema:'eris.artifact.v1',artifact_id:'art_'+'d'.repeat(24),turn_id:'turn:1',revision:1,
 source:'tutor_activity',kind:'document',content:{format:'markdown',source:'Question',activity:{...base,response_mode:mode}},presentation:{title:'Học tập'}};}
test('numeric input renders real form with no correct key',()=>{
 const html=renderer.render(artifact('numeric')).html;
 assert.match(html,/learning-text-answer/);
 assert.match(html,/data-learning-action="answer"/);
 assert.doesNotMatch(html,/expected_text|correct_index|data-learning-choice/);
});
test('reflection input is ungraded, not a fake correct button',()=>{
 const html=renderer.render(artifact('reflection')).html;
 assert.match(html,/chưa tự chấm/);
 assert.match(html,/<textarea /);
});
test('foreign artifact and invalid activity IDs never create answer controls',()=>{
 const a=artifact('numeric');a.source='root_presentation';
 assert.doesNotMatch(renderer.render(a).html,/data-learning-action/);
 const b=artifact('numeric');b.content.activity.activity_id='invalid';
 assert.doesNotMatch(renderer.render(b).html,/data-learning-action/);
});
