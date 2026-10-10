(() => {
  const $ = selector => document.querySelector(selector);
  const state = {data:null, categories:null, policies:[], editing:null, bulk:null, timer:null};
  const csrf = () => document.querySelector('meta[name="csrf-token"]').content;
  const request = async (url, options={}) => {
    const headers = {...(options.headers||{})};
    if (options.body) headers["Content-Type"] = "application/json";
    if (options.method && options.method !== "GET") headers["X-CSRF-Token"] = csrf();
    const response = await fetch(url,{...options,headers});
    if (response.status === 401) { location.assign("/login"); throw new Error("登录已失效"); }
    const body = response.headers.get("content-type")?.includes("json") ? await response.json() : await response.text();
    if (!response.ok) throw new Error(body?.error || `请求失败（${response.status}）`);
    return body;
  };
  const notice = (message,error=false) => {const node=$("#global-notice");node.textContent=message;node.classList.toggle("error",error);node.hidden=!message;};
  const categoryName = key => state.categories?.[key]?.label || state.data?.categories?.[key]?.label || key;
  const effectiveCategory = id => ({...state.categories[id],...(state.data.state.categories[id]||{})});
  const dirty = () => {const draft=$("#draft-label");draft.textContent="本机草稿";draft.className="tag warning";$("#sync-pill").textContent="有未同步修改";$("#sync-pill").className="status-pill warning";stage("sync","active");};
  function stage(name,mode){const el=document.querySelector(`.flow-step[data-stage="${name}"]`);if(el){el.classList.remove("active","done");if(mode)el.classList.add(mode);}}
  function render(){
    renderRules();renderCategories();renderTasks();renderReleases();
    const remote=state.data.remote_status,remoteNode=$("#remote-check-status");
    if(remote?.status==="ok"){remoteNode.textContent=`远端检查 ${remote.checked_at} · ${remote.remote_changed?"rules-data 有新提交":"规则数据未变化"} · 最新规则 Release ${remote.latest_release_tag||"暂无"}`;}
    else if(remote?.status==="error"){remoteNode.textContent=`远端检查失败：${remote.error} · ${remote.checked_at||"时间未知"}`;}
    if(state.data.dirty){dirty();}else{$("#draft-label").textContent="与 GitHub 一致";$("#draft-label").className="tag success";$("#sync-pill").textContent="GitHub 已同步";$("#sync-pill").className="status-pill success";stage("sync","done");}
    const running=state.data.tasks.some(task=>["queued","running","polling","downloading"].includes(task.status));
    if(running)stage("ci","active");else if(state.data.tasks.some(task=>task.kind==="build"&&task.status==="succeeded"))stage("ci","done");
    if(state.data.candidate_release_id)stage("candidate","done");
  }
  function renderRules(){
    const body=$("#rules-table");body.replaceChildren();
    const query=$("#rule-search").value.trim().toLowerCase(),categoryFilter=$("#rule-filter-category").value,statusFilter=$("#rule-filter-status").value;
    const visible=state.data.state.rules.map((rule,index)=>({rule,index})).filter(({rule})=>(!query||rule.domain.toLowerCase().includes(query))&&(!categoryFilter||rule.category===categoryFilter)&&(!statusFilter||(statusFilter==="disabled")===(rule.enabled===false)));
    visible.forEach(({rule,index})=>{
      const row=document.createElement("tr");
      const domain=document.createElement("td");domain.className="domain-cell";domain.textContent=rule.domain;row.append(domain);
      const match=document.createElement("td");match.textContent=rule.match_type==="EXACT"?"精确":"后缀";row.append(match);
      const category=document.createElement("td"),select=document.createElement("select");
      Object.keys(state.categories).forEach(id=>{const option=document.createElement("option");option.value=id;option.textContent=`${state.categories[id].label} · ${id}`;option.selected=id===rule.category;select.append(option);});
      select.addEventListener("change",()=>{rule.category=select.value;dirty();});category.append(select);row.append(category);
      const enabled=document.createElement("td"),toggle=document.createElement("button");toggle.className="quiet";toggle.textContent=rule.enabled===false?"已停用":"启用中";toggle.setAttribute("aria-pressed",String(rule.enabled!==false));toggle.addEventListener("click",()=>{rule.enabled=rule.enabled===false;dirty();renderRules();});enabled.append(toggle);row.append(enabled);
      const actions=document.createElement("td"),edit=document.createElement("button"),remove=document.createElement("button");edit.className="quiet";edit.textContent="编辑";edit.addEventListener("click",()=>{state.editing=index;$("#rule-domain").value=rule.domain;$("#rule-match").value=rule.match_type;$("#rule-category").value=rule.category;$("#rule-note").value=rule.note||"";$("#rule-add").textContent="保存修改";$("#rule-domain").focus();});remove.className="quiet";remove.textContent="删除";remove.addEventListener("click",()=>{state.data.state.rules.splice(index,1);dirty();renderRules();});actions.append(edit,remove);row.append(actions);body.append(row);
    });
    if(!visible.length){const row=document.createElement("tr"),cell=document.createElement("td");cell.colSpan=5;cell.className="empty";cell.textContent=state.data.state.rules.length?"没有符合筛选条件的规则。":"没有自定义规则。添加一条域名规则开始。";row.append(cell);body.append(row);}
  }
  function renderCategories(){
    const body=$("#categories-table");body.replaceChildren();
    Object.entries(state.categories).forEach(([id,base])=>{
      const cfg=effectiveCategory(id),row=document.createElement("tr");
      const name=document.createElement("td"),bold=document.createElement("strong");bold.textContent=base.label;name.append(bold);const small=document.createElement("small");small.textContent=` ${id}`;small.className="muted";name.append(small);row.append(name);
      const on=document.createElement("td"),check=document.createElement("input");check.type="checkbox";check.checked=cfg.enabled!==false;check.setAttribute("aria-label",`${base.label} 启用`);check.addEventListener("change",()=>{state.data.state.categories[id]={...state.data.state.categories[id],enabled:check.checked};dirty();});on.append(check);row.append(on);
      const policy=document.createElement("td"),select=document.createElement("select");select.setAttribute("aria-label",`${base.label} 策略组`);state.data.policies.forEach(item=>{const option=document.createElement("option");option.value=item;option.textContent=item;option.selected=item===cfg.policy_group;select.append(option);});select.addEventListener("change",()=>{state.data.state.categories[id]={...state.data.state.categories[id],policy_group:select.value};dirty();});policy.append(select);row.append(policy);
      const priority=document.createElement("td"),number=document.createElement("input");number.type="number";number.className="priority-input";number.value=cfg.priority;number.setAttribute("aria-label",`${base.label} 优先级`);number.addEventListener("change",()=>{state.data.state.categories[id]={...state.data.state.categories[id],priority:Number(number.value)};dirty();});priority.append(number);row.append(priority);
      const source=document.createElement("td"),details=document.createElement("details"),summary=document.createElement("summary");summary.textContent="查看";const code=document.createElement("code");code.textContent=base.source_file||"自定义";details.append(summary,code);source.append(details);row.append(source);body.append(row);
    });
  }
  function renderTasks(){
    const tasks=state.data.tasks||[];const list=$("#task-list"),history=$("#history-table");list.replaceChildren();history.replaceChildren();
    if(!tasks.length){list.innerHTML='<p class="empty-state">暂时没有任务。</p>';history.innerHTML='<p class="empty-state">等待载入。</p>';return;}
    tasks.slice(0,8).forEach(task=>{const row=document.createElement("div");row.className=`task-row ${task.status}`;const title=document.createElement("strong");title.textContent=`${task.kind==="save"?"保存规则":"生成规则"} · ${statusText(task.status)}`;const time=document.createElement("small");time.textContent=task.updated_at;const msg=document.createElement("p");msg.textContent=task.message;row.append(title,time,msg);list.append(row);});
    const table=document.createElement("table");table.innerHTML="<thead><tr><th>时间</th><th>操作</th><th>状态</th><th>记录</th></tr></thead>";const tbody=document.createElement("tbody");tasks.forEach(task=>{const row=document.createElement("tr");[task.updated_at,task.kind==="save"?"保存规则":"生成规则",statusText(task.status),task.message].forEach(value=>{const cell=document.createElement("td");cell.textContent=value;row.append(cell);});tbody.append(row);});table.append(tbody);history.append(table);
  }
  function statusText(value){return ({queued:"排队中",running:"运行中",polling:"等待 CI",downloading:"下载校验",succeeded:"已完成",failed:"失败",blocked:"受阻",interrupted:"中断"})[value]||value;}
  function renderReleases(){
    const releases=state.data.releases||[],detail=$("#candidate-detail"),links=$("#report-links"),history=$("#release-history");links.replaceChildren();history.replaceChildren();
    const candidateId=state.data.candidate_release_id,current=releases.find(item=>item.release_id===candidateId);
    if(!current){detail.textContent=releases.length?"最近下载的规则不是当前候选（输入快照已过期或候选未提升）。":"还没有下载过规则版本。";detail.className="candidate-detail empty-state";$("#candidate-short").textContent=candidateId?candidateId.slice(0,12):"尚无";$("#candidate-time").textContent=candidateId?"当前候选版本":"等待首个校验版本";if(releases.length){const heading=document.createElement("p");heading.className="eyebrow";heading.textContent="已校验历史版本（非候选）";history.append(heading);releases.forEach(item=>{const row=document.createElement("div");row.className="release-row";const id=document.createElement("span");id.className="candidate-id";id.textContent=item.release_id.slice(0,12);const time=document.createElement("small");time.textContent=item.created_at;row.append(id,time);history.append(row);});}return;}
    const manifest=current.metadata?.manifest||{};$("#candidate-short").textContent=current.release_id.slice(0,12);$("#candidate-time").textContent=`CI 完成 ${current.metadata?.completed_at||"时间未知"}`;
    detail.replaceChildren();detail.className="candidate-detail";const id=document.createElement("div");id.className="candidate-id";id.textContent=current.release_id;detail.append(id);const stats=document.createElement("div");stats.className="candidate-stats";[["规则",manifest.statistics?.rule_count??"—"],["分类",Object.keys(manifest.category_status||{}).length||"—"],["来源 SHA",current.data_commit.slice(0,8)]].forEach(([label,value])=>{const box=document.createElement("div");box.className="stat";const strong=document.createElement("strong");strong.textContent=value;const small=document.createElement("span");small.textContent=label;box.append(strong,small);stats.append(box);});detail.append(stats);
    const deployStatus=document.createElement("p");deployStatus.className=manifest.deployment_allowed?"tag success":"tag warning";deployStatus.textContent=manifest.deployment_allowed?"构建门禁允许部署（尚未部署）":"部署受阻";detail.append(deployStatus);
    const runtime=document.createElement("p");runtime.className="small muted";runtime.textContent="本版本是 NAS 候选；目标设备当前运行版本未由此页面确认。";detail.append(runtime);
    const blockers=manifest.deployment_blockers||[];if(blockers.length){const review=document.createElement("details"),summary=document.createElement("summary"),list=document.createElement("ul");summary.textContent=`查看部署阻断原因（${blockers.length}）`;blockers.forEach(item=>{const li=document.createElement("li"),parts=[item.reason||"未分类原因"];if(item.domain)parts.push(`${item.match_type||"域名"} ${item.domain}`);if(item.categories?.length)parts.push(`类别：${item.categories.join("、")}`);if(item.entries?.length)parts.push(`审核项：${item.entries.join("、")}`);if(item.count!==undefined)parts.push(`数量：${item.count}`);if(item.category)parts.push(`类别：${item.category}`);li.textContent=parts.join(" · ");list.append(li);});review.append(summary,list);detail.append(review);}
    ["statistics.json","shared-domains.json","excluded-rules.json","conflicts.json","unsupported.json","source-inventory.json"].forEach(name=>{const a=document.createElement("a");a.className="text-link";a.href=`/api/report/${current.release_id}/${name}`;a.textContent=name.replace(".json","");links.append(a);});
    loadCandidateDownloads(current.release_id);
    const previous=releases.filter(item=>item.release_id!==current.release_id);if(previous.length){const heading=document.createElement("p");heading.className="eyebrow";heading.textContent="此前校验版本";history.append(heading);previous.forEach(item=>{const row=document.createElement("div");row.className="release-row";const id=document.createElement("span");id.className="candidate-id";id.textContent=item.release_id.slice(0,12);const time=document.createElement("small");time.textContent=item.created_at;row.append(id,time);history.append(row);});}
  }
  function addRule(rule){
    if(!rule.domain||!rule.category||!rule.match_type)throw new Error("请填写域名、匹配方式和类别");
    const pair=state.data.state.rules.find((item,index)=>index!==state.editing&&item.domain===rule.domain.toLowerCase()&&item.match_type===rule.match_type);
    if(pair)throw new Error("已有相同匹配方式的域名规则");
    if(state.editing===null)state.data.state.rules.push(rule);else state.data.state.rules[state.editing]=rule;
    state.editing=null;$("#rule-domain").value="";$("#rule-note").value="";$("#rule-add").textContent="添加规则";dirty();renderRules();
  }
  $("#rule-add").addEventListener("click",()=>{try{addRule({domain:$("#rule-domain").value.trim().toLowerCase(),match_type:$("#rule-match").value,category:$("#rule-category").value,...($("#rule-note").value.trim()?{note:$("#rule-note").value.trim()}:{}),...(state.editing!==null&&state.data.state.rules[state.editing].enabled===false?{enabled:false}:{})});}catch(error){notice(error.message,true);}});
  $("#bulk-preview").addEventListener("click",async()=>{const out=$("#bulk-result"),button=$("#bulk-apply");button.disabled=true;state.bulk=null;out.hidden=false;out.textContent="正在校验域名、类别和重复项…";try{const result=await request("/api/import-preview",{method:"POST",body:JSON.stringify({lines:$("#bulk-input").value})});const rows=result.rules.map(x=>`第 ${x.line} 行 · ${x.domain} · ${x.match_type} · ${x.category}`),errors=result.errors.map(x=>`第 ${x.line} 行错误 · ${x.input} · ${x.error}`);out.textContent=`通过 ${result.rules.length} 条 · 错误 ${result.errors.length} 条\n${[...errors,...rows].join("\n")||"没有可导入规则。"}`;if(result.ok){state.bulk=result.rules.map(({line,...rule})=>rule);button.disabled=false;}}catch(error){out.textContent=error.message;notice(error.message,true);}});
  $("#bulk-apply").addEventListener("click",()=>{for(const rule of state.bulk||[]){if(!state.data.state.rules.some(existing=>existing.domain===rule.domain&&existing.match_type===rule.match_type))state.data.state.rules.push(rule);}state.bulk=null;dirty();renderRules();$("#bulk-apply").disabled=true;notice("预览通过的规则已加入本机草稿；请保存到 GitHub。");});
  $("#save-button").addEventListener("click",async()=>{try{await request("/api/draft",{method:"PUT",body:JSON.stringify({state:state.data.state})});state.data.dirty=true;render();const task=await request("/api/save",{method:"POST",body:"{}"});notice("保存任务已排队；GitHub 同步完成后再生成规则。");await poll(task.task_id);}catch(error){notice(error.message,true);}});
  $("#reload-button").addEventListener("click",async()=>{if(!confirm("从 GitHub 重新载入会丢弃当前本机草稿。继续吗？"))return;try{const result=await request("/api/reload",{method:"POST",body:"{}"});state.data=await request("/api/state");render();notice(`已载入 GitHub 规则 ${result.synced_commit.slice(0,8)}。`);}catch(error){notice(error.message,true);}});
  $("#build-button").addEventListener("click",async()=>{try{const task=await request("/api/build",{method:"POST",body:"{}"});notice("GitHub Actions 已开始处理规则。");await poll(task.task_id);}catch(error){notice(error.message,true);}});
  async function poll(taskId){if(state.timer)clearInterval(state.timer);const once=async()=>{try{const result=await request("/api/tasks");state.data.tasks=result.tasks;renderTasks();const task=result.tasks.find(x=>x.id===taskId);if(task&&["succeeded","failed","blocked","interrupted"].includes(task.status)){clearInterval(state.timer);state.timer=null;const reload=await request("/api/state");state.data={...state.data,...reload};render();notice(task.status==="succeeded"?`${task.kind==="save"?"规则已保存到 GitHub":"候选版本下载并校验完成"}。`:task.message,task.status!=="succeeded");}}catch(error){notice(error.message,true);}};await once();state.timer=setInterval(once,3000);}
  $("#refresh").addEventListener("click",async()=>{try{state.data=await request("/api/state");render();notice("已刷新本地记录。");}catch(error){notice(error.message,true);}});
  $("#diff-button").addEventListener("click",async()=>{try{$("#inspect-result").hidden=false;$("#inspect-result").textContent=JSON.stringify(await request("/api/diff"),null,2);}catch(error){notice(error.message,true);}});
  $("#rule-search").addEventListener("input",renderRules);$("#rule-filter-category").addEventListener("change",renderRules);$("#rule-filter-status").addEventListener("change",renderRules);
  $("#explain-button").addEventListener("click",async()=>{const domain=prompt("输入要解释的域名");if(!domain)return;try{const result=await request(`/api/explain?domain=${encodeURIComponent(domain)}`),box=$("#inspect-result");box.replaceChildren();box.hidden=false;const pre=document.createElement("pre");pre.textContent=JSON.stringify(result,null,2);box.append(pre);for(const row of result.matches||[]){if(row.custom)continue;const button=document.createElement("button");button.className="secondary compact";button.textContent=`排除此条来源规则 · ${row.category} · ${row.match_type} ${row.domain}`;button.addEventListener("click",async()=>{const reviewer=prompt("审核人（公开记录）");if(!reviewer)return;const reason=prompt("排除理由（公开记录）");if(!reason)return;try{await request("/api/exclusions",{method:"POST",body:JSON.stringify({category:row.category,domain:row.domain,match_type:row.match_type,reviewer,reason})});state.data=await request("/api/state");state.data.dirty=true;render();notice("精确来源排除已加入本机草稿；保存到 GitHub 后才会进入构建。");}catch(error){notice(error.message,true);}});box.append(button);}}catch(error){notice(error.message,true);}});
  $("#download-history").addEventListener("click",async()=>{try{const result=await request("/api/audit");const blob=new Blob([JSON.stringify(result,null,2)],{type:"application/json"}),url=URL.createObjectURL(blob),link=document.createElement("a");link.href=url;link.download="dns-rule-audit.json";link.click();URL.revokeObjectURL(url);}catch(error){notice(error.message,true);}});
  async function loadCandidateDownloads(releaseId){try{const data=await request(`/api/files/${releaseId}`),box=$("#candidate-downloads");box.replaceChildren();const bundle=document.createElement("a");bundle.className="text-link";bundle.href=data.bundle;bundle.textContent="完整规则包 ZIP";box.append(bundle);data.files.forEach(path=>{const a=document.createElement("a");a.className="text-link";a.href=`/api/file/${releaseId}/${path.split("/").map(encodeURIComponent).join("/")}`;a.download=path.split("/").at(-1);a.textContent=path;box.append(a);});}catch(error){notice(error.message,true);}}
  $("#logout").addEventListener("click",async()=>{try{await request("/logout",{method:"POST",body:""});location.assign("/login");}catch(error){notice(error.message,true);}});
  async function load(){try{state.data=await request("/api/state");state.categories=Object.fromEntries(Object.entries(state.data.categories).map(([id,cfg])=>[id,{...cfg,label:cfg.provider_name}]));state.policies=state.data.policies;const selector=$("#rule-category"),filter=$("#rule-filter-category");Object.entries(state.categories).forEach(([id,cfg])=>{const option=document.createElement("option");option.value=id;option.textContent=`${cfg.label} · ${id}`;selector.append(option);const filterOption=option.cloneNode(true);filter.append(filterOption);});render();}catch(error){notice(error.message,true);}}
  load();
})();
