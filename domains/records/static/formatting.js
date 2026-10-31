/* Deliberately small Markdown subset. All content is text nodes, never HTML. */
window.RecordsFormatting={render(text){
  const root=document.createElement('div');root.className='event-body formatted-message';
  function inline(parent,value,depth=0){
    if(depth>3){parent.append(document.createTextNode(value));return;}
    const re=/(`[^`\n]+`|\*\*[^*\n]+\*\*|\*[^*\n]+\*|\[[^\[\]\n]+\]\([^\s()]+\))/g;
    let at=0;for(const match of value.matchAll(re)){
      parent.append(document.createTextNode(value.slice(at,match.index)));const raw=match[0];let node;
      if(raw[0]==='`'){node=document.createElement('code');node.textContent=raw.slice(1,-1);}
      else if(raw.startsWith('**')){node=document.createElement('strong');inline(node,raw.slice(2,-2),depth+1);}
      else if(raw[0]==='*'){node=document.createElement('em');inline(node,raw.slice(1,-1),depth+1);}
      else{const parts=/^\[([^\]]+)\]\((.+)\)$/.exec(raw);try{const url=new URL(parts[2]);if(!['http:','https:'].includes(url.protocol)||url.username||url.password)throw Error();node=document.createElement('a');node.href=url.href;node.rel='noopener noreferrer';node.target='_blank';node.textContent=parts[1]===url.href?parts[1]:`${parts[1]} (${url.host})`;node.title=url.href;}catch{node=document.createTextNode(raw);}}
      parent.append(node);at=match.index+raw.length;
    }parent.append(document.createTextNode(value.slice(at)));
  }
  const lines=String(text).split('\n');let fence=null,code=[],list=null;
  for(const line of lines){
    if(line.startsWith('```')){if(fence){const pre=document.createElement('pre'),node=document.createElement('code');node.textContent=code.join('\n');pre.append(node);root.append(pre);fence=null;code=[];}else{fence=true;list=null;}continue;}
    if(fence){code.push(line);continue;}
    const bullet=/^\s*(?:[-*] |\d+\. )(.*)$/.exec(line);
    if(bullet){const type=/^\s*\d/.test(line)?'ol':'ul';if(!list||list.tagName.toLowerCase()!==type){list=document.createElement(type);root.append(list);}const item=document.createElement('li');inline(item,bullet[1]);list.append(item);continue;}
    list=null;const node=document.createElement('div');if(line)inline(node,line);else node.append(document.createElement('br'));root.append(node,document.createTextNode('\n'));
  }
  if(fence){const pre=document.createElement('pre');pre.textContent='```\n'+code.join('\n');root.append(pre);}
  return root;
}};
