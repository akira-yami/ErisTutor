const { test } = require('node:test');
const assert = require('node:assert/strict');
const R = require('../chat.js');

test('real LaTeX display math and inline math', () => {
  const output = R.render('Giải $x^2$\n\n$$\\int_0^1 x\\,dx=\\frac12$$');
  assert.match(output, /math-inline/);
  assert.match(output, /math-block/);
  assert.match(output, /\\frac12/);
});
test('old Unicode integral is upgraded without changing original text', () => {
  const input = '∫₀¹ x dx = x²/2 |₀¹ = 1/2.';
  assert.match(R.render(input), /\\int_\{0\}\^\{1\}/);
  assert.match(R.render(input), /\\frac\{x\^\{2\}\}\{2\}/);
  assert.equal(input, '∫₀¹ x dx = x²/2 |₀¹ = 1/2.');
});
test('heading, list, quote, code and GFM-like table', () => {
  const out = R.render('# Chủ đề\n\n- một\n- hai\n\n> lưu ý\n\n|x|y|\n|---|---|\n|1|2|\n\n```python\nprint(1)\n```');
  for(const fragment of ['<h1>','<ul>','<blockquote>','<table>','code-card','language-python']) assert.ok(out.includes(fragment));
});
test('unsafe model HTML is inert', () => {
  const out=R.render('<img src=x onerror="alert(1)">\n\n<script>alert(1)</script>');
  assert.ok(!out.includes('<img'));
  assert.ok(!out.includes('<script'));
  assert.ok(out.includes('&lt;img'));
});
test('safe clickable links, no javascript URL scheme', () => {
  const out=R.render('[Trang chủ](https://example.org) và [x](javascript:alert(1))');
  assert.ok(out.includes('rel="noopener noreferrer"'));
  assert.ok(!out.includes('href="javascript:'));
});
test('spoken long answers split for readability without losing sentences', () => {
  const first='Câu đầu tiên nói về hệ thống. ';
  const input=first.repeat(26);
  const out=R.render(input);
  assert.ok((out.match(/<p>/g)||[]).length>1);
  assert.equal((out.match(/Câu đầu tiên nói về hệ thống/g)||[]).length,26);
});
test('inline code does not become bold or HTML', () => {
  const out=R.render('`**hello** <script>` and **bold**');
  assert.ok(out.includes('<code>**hello** &lt;script&gt;</code>'));
  assert.ok(out.includes('<strong>bold</strong>'));
});
test('chart line and bar fences render real SVG with data labels', () => {
  const spec='```chart\n'+JSON.stringify({type:'bar',title:'Doanh thu',labels:['T1','T2','T3'],values:[10,20,15]})+'\n```';
  const out=R.render(spec);
  assert.ok(out.includes('<svg'));
  assert.ok(out.includes('chart-bar'));
  assert.ok(out.includes('Doanh thu'));
});
test('safe mathematical function plot does not evaluate arbitrary JS', () => {
  assert.equal(typeof R.safeExpression('sin(x)+x^2'), 'function');
  assert.equal(R.safeExpression('alert(1)'),null);
  assert.equal(R.safeExpression('x.constructor'),null);
  const out=R.render('```plot\n{"expression":"sin(x)","xmin":-3.14,"xmax":3.14}\n```');
  assert.ok(out.includes('Đồ thị: y = sin(x)'));
  assert.ok(out.includes('chart-line'));
});

test('double-serialized LaTeX from the real conversation is rendered as math', () => {
  const raw = String.raw`Lại nhé: \\(\\int\\_0^1 x \\,dx\\), rồi \\[\\frac{1}{2}\\]`;
  const out = R.render(raw);
  assert.match(out,/math-inline/);
  assert.match(out,/math-block/);
  assert.ok(out.includes(String.raw`\int_0^1`));
  assert.ok(out.includes(String.raw`\frac{1}{2}`));
  assert.ok(!out.includes(String.raw`\\(`));
  assert.ok(!out.includes(String.raw`\\,`));
});
test('no destructive backslash normalization in code blocks', () => {
  const source = String.raw`\(\int\)`;
  const out = R.render('```text\n' + source + '\n```');
  assert.ok(out.includes(source));
  const doubled=String.raw`\\(\\int\\)`;
  assert.ok(R.render('```text\n'+doubled+'\n```').includes(doubled));
  assert.ok(R.render('`'+doubled+'`').includes(doubled));
});
test('active audio indication uses exact turn id', () => {
  const L=R.ErisLive;
  const speaking=L.speech({speech:{phase:'speaking',turnId:'delivery:abc'}});
  assert.equal(L.matches({role:'assistant',turnId:'delivery:abc'},speaking),true);
  assert.equal(L.matches({role:'assistant',turnId:'delivery:stale'},speaking),false);
  assert.equal(L.matches({role:'user',turnId:'delivery:abc'},speaking),false);

});
