import fs from 'node:fs';import vm from 'node:vm';import assert from 'node:assert/strict';import path from 'node:path';import os from 'node:os';
const source=fs.readFileSync(process.argv[2],'utf8');
const fn=source.slice(source.indexOf('function loadJobs() {'),source.indexOf('\nfunction hasTombstone('));
assert.ok(!fn.includes('unlinkSync'));const root=fs.mkdtempSync(path.join(os.tmpdir(),'relay-audit-fixture-'));try{
fs.mkdirSync(path.join(root,'jobs'));fs.mkdirSync(path.join(root,'tombstones'));
const rows=[{id:'j-1111111111111111',state:'completed',createdAt:1,endedAt:2},{id:'j-2222222222222222',state:'running',createdAt:1},{id:'j-3333333333333333',state:'unknown',createdAt:1}];
for(const row of rows)fs.writeFileSync(path.join(root,'jobs',row.id+'.json'),JSON.stringify(row));
fs.writeFileSync(path.join(root,'jobs','j-4444444444444444.json'),'malformed');fs.writeFileSync(path.join(root,'tombstones','j-1111111111111111'),'1');
const before=fs.readFileSync(path.join(root,'jobs',rows[0].id+'.json'));const jobs=new Map(),persisted=[];
vm.runInNewContext(fn+';loadJobs();',{fs,path,STATE_DIR:root,JOB_ID:/^j-[0-9a-f]{16}$/,Date,Map,jobs,persist:j=>persisted.push({...j})});
assert.equal(jobs.size,3);assert.equal(jobs.get(rows[0].id).state,'completed');assert.deepEqual(fs.readFileSync(path.join(root,'jobs',rows[0].id+'.json')),before);
assert.equal(jobs.get(rows[1].id).state,'unknown');assert.equal(jobs.get(rows[1].id).errorClass,'relay_restarted');assert.equal(persisted.length,1);
assert.equal(jobs.get(rows[2].id).state,'unknown');assert.equal(fs.readFileSync(path.join(root,'tombstones',rows[0].id),'utf8'),'1');assert.equal(fs.readFileSync(path.join(root,'jobs','j-4444444444444444.json'),'utf8'),'malformed');
console.log('5 retention checks passed: old audit retained; interrupted job unknown; preexisting unknown preserved; malformed retained; tombstone retained');
}finally{fs.rmSync(root,{recursive:true,force:true});}
