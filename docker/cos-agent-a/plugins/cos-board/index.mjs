import { definePluginEntry } from 'openclaw/plugin-sdk/plugin-entry';
import { executeBoard } from './client.mjs';
const text={type:'string',minLength:1,maxLength:280};
const label={type:['string','null'],enum:['decide','blocked','remember','followup','idea','fyi',null]};
export default definePluginEntry({id:'cos-board',name:'CoS Board',register(api){
  api.registerTool({name:'cos_board',label:'CoS Board',
    description:'Read and write Robert’s CoS Board using the same Board service as MC, scoped to CoS. Use save only when Robert asks to keep a note; preserve his language, maximum 280 characters, never truncate. List/read to identify existing notes; clarify ambiguous targets. Read the current semantic_revision before update or undo and pass expected_revision. update changes text, label or done (true to complete, false to reopen). result recovers a prior operation receipt. undo reverses your own specified saved operation only when Robert asks; no force or permanent delete. Only report saved when state is verified with a receipt. On unknown, retain operation_id and use result; never create a replacement. Board and Actions are separate. Photos use the Board UI. Never copy private material to a room without Robert’s permission.',
    parameters:{type:'object',additionalProperties:false,required:['operation'],properties:{
      operation:{type:'string',enum:['list','read','save','update','result','undo','capabilities']},
      query:{type:'string',maxLength:200},state:{type:'string',enum:['active','done','all']},limit:{type:'integer',minimum:1,maximum:20},cursor:{type:'string'},
      note_ref:{type:'string'},operation_ref:{type:'string'},expected_revision:{type:'integer',minimum:1},text,label,
      changes:{type:'object',additionalProperties:false,minProperties:1,properties:{text,label,done:{type:'boolean'}}}}},
    execute:async(id,args)=>executeBoard(id,args)});
}});
