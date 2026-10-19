import { definePluginEntry } from 'openclaw/plugin-sdk/plugin-entry';
import { randomUUID } from 'node:crypto';
import { writeFile, readFile, rename } from 'node:fs/promises';
const ROOT='/proving-queue';
async function call(method, args) {
  const id=randomUUID();
  const request=JSON.stringify({id,method,args});
  if(request.length>8192) throw new Error('Tool arguments too large');
  await writeFile(`${ROOT}/${id}.tmp`,request,{flag:'wx',mode:0o600});
  await rename(`${ROOT}/${id}.tmp`,`${ROOT}/${id}.request`);
  const deadline=Date.now()+110000;
  while(Date.now()<deadline){
    try {
      const raw=await readFile(`${ROOT}/${id}.response`,'utf8');
      if(raw.length>300000)throw new Error('Tool response too large');
      const result=JSON.parse(raw);
      return {content:[{type:'text',text:JSON.stringify(result)}],details:result};
    }catch(e){if(e.code!=='ENOENT')throw e;}
    await new Promise(resolve=>setTimeout(resolve,200));
  }
  throw new Error('Tool result unknown; stop and ask the operator to reconcile. Do not retry.');
}
const str={type:'string',maxLength:240};
const tools=[
 ['mc_repo_read','Read an allowlisted source file at a frozen revision; returns source hash and numbered window.',{revision:str,path:str,start:{type:'integer',minimum:1},count:{type:'integer',minimum:1,maximum:200}},['revision','path']],
 ['mc_repo_diff','Compare one allowlisted source file between frozen revisions.',{before:str,after:str,path:str},['before','after','path']],
 ['mc_run_tests','Run one fixed Board test suite in an isolated no-network test container.',{suite:{type:'string',enum:['board_commands','board_multisentence']}},['suite']],
 ['mc_repo_changes','List what a pinned commit changed: its subject, its parent commits (with any allowlisted labels, for mc_repo_diff), and each changed file path with its status and whether mc_repo_read can open it. Paths and status only, no file contents.',{revision:str},['revision']],
];
export default definePluginEntry({id:'mc-proving-tools',name:'MC proving tools',register(api){
 for(const [name,description,properties,required] of tools)api.registerTool({name,label:name,description,parameters:{type:'object',properties,required,additionalProperties:false},execute:async(_id,args)=>call(name,args)});
}});
