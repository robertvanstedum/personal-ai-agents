import { definePluginEntry } from 'openclaw/plugin-sdk/plugin-entry';
import { executeActions } from './client.mjs';
const str = {type:'string'};
export default definePluginEntry({id:'cos-actions',name:'CoS Actions',register(api){
  api.registerTool({name:'cos_actions',label:'CoS Actions',
    description:'Use Robert’s Actions workspace to keep agreed work and archive briefings. Types and priorities are independent of status. This records work; it does not perform external actions. List/get before changing an existing item; pass its current revision. Use open for a new record (default assigned to CoS), comment for details or briefing content (up to 4000 characters; split longer content into ordered comments), update for progress, close with done or dropped for completed or cancelled. Only say saved when result.outcome is accepted. On failure or timeout, do not recreate or blindly retry: list/get to reconcile first. Never claim an action was performed just because it was recorded.',
    parameters:{type:'object',additionalProperties:false,required:['operation'],properties:{
      operation:{type:'string',enum:['list','get','open','update','comment','close']},
      item_id:str, revision:{type:'integer',minimum:1},offset:{type:'integer',minimum:0},title:{type:'string',maxLength:200},
      kind:{type:'string',enum:['action','briefing','follow-up','research','decision','risk']},
      priority:{type:'integer',minimum:1,maximum:3},status:{type:'string',enum:['open','doing','waiting']},
      with:{type:'string',enum:['cos','robert','mc']},text:{type:'string',maxLength:4000},note:{type:'string',maxLength:2000},outcome:{type:'string',enum:['done','dropped']}}},
    execute:async(id,args)=>executeActions(id,args)});
}});
