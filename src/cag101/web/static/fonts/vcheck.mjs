import { validate, validateOrdinal, contrast } from './vp.mjs';
const S = '#ffffff';
console.log("### criterion series vs WHITE surface");
console.log(JSON.stringify(validate(['#2a78d6','#eb6834','#1baf7a','#eda100'], {mode:'light', surface:S}), null, 1));
console.log("\n### 5-step ordinal ramp vs WHITE");
console.log(JSON.stringify(validateOrdinal(['#86b6ef','#5598e7','#2a78d6','#1c5cab','#104281'], {mode:'light', surface:S}), null, 1));
console.log("\n### UI contrast checks (text/controls on white)");
for (const [n,h] of [["brand-500 #256abf",'#256abf'],["brand-550 #1c5cab",'#1c5cab'],["ink-2 #475467",'#475467'],["ink-3 #7d8797",'#7d8797'],["critical #b02a2a",'#b02a2a'],["success #006300",'#006300']])
  console.log(`  ${n}: ${contrast(h,S).toFixed(2)}:1`);
