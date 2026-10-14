(() => {
  const $ = selector => document.querySelector(selector);
  const displayTimeFormat = new Intl.DateTimeFormat("zh-CN", {timeZone:"Asia/Shanghai",year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit",hourCycle:"h23"});
  const displayTime = value => {
    if (!value) return "时间未知";
    const date = new Date(value);
    return Number.isFinite(date.getTime()) ? `${displayTimeFormat.format(date)} 北京时间` : "时间未知";
  };
  const state = {data:null, categories:null, policies:[], editing:null, bulk:null, timer:null, catalogSelected:new Set(), batchAction:null, browserDirty:false, editRevision:0, previewSequence:0, batchPreviewRevision:null, batchPreviewIds:null, diff:null, diffIdentity:null, diffPending:null, preferredPreparationRequest:null, rollbackExplicit:false, strategy:{data:null,loading:false,error:null,groupName:null,editorMembers:new Set(),selectedChoice:null,draftSelectedChoice:null,preview:null,previewing:false,previewSerial:0,busy:false,operation:null,taskTimer:null,taskId:null,taskMessage:""},busy:{save:false,build:false,deployment:false}};
  const csrf = () => document.querySelector('meta[name="csrf-token"]').content;
  const request = async (url, options={}) => {
    const headers = {...(options.headers||{})};
    if (options.body) headers["Content-Type"] = "application/json";
    if (options.method && options.method !== "GET") headers["X-CSRF-Token"] = csrf();
    const response = await fetch(url,{...options,headers});
    if (response.status === 401) { location.assign("/login"); throw new Error("登录已失效"); }
    const body = response.headers.get("content-type")?.includes("json") ? await response.json() : await response.text();
    if (!response.ok) {const error=new Error(body?.error || `请求失败（${response.status}）`);error.details=body;throw error;}
    return body;
  };
  const notice = (message,error=false) => {const node=$("#global-notice");node.textContent=message;node.classList.toggle("error",error);node.hidden=!message;};
  const categoryName = key => state.categories?.[key]?.label || state.data?.categories?.[key]?.label || key;
  const effectiveCategory = id => ({...state.categories[id],...(state.data.state.categories[id]||{})});
  const catalogReadOnly = item => !["proxy","review"].includes(item.route_intent)||["reference_only","prohibited_aggregate","manual_missing"].includes(item.implementation_status)||item.missing;
  const dirty = () => {state.browserDirty=true;state.editRevision++;invalidateBatchPreview();setDirtyLabel();renderActionControls();};
  function setDirtyLabel(){renderDraftStatus();}
  function invalidateBatchPreview(){state.previewSequence++;state.batchAction=null;state.batchPreviewRevision=null;state.batchPreviewIds=null;$("#batch-apply").disabled=true;if($("#batch-result"))$("#batch-result").hidden=true;}
  function mergeServerData(incoming){const localDraft=state.data?.state;state.data={...state.data,...incoming};if(state.browserDirty&&localDraft)state.data.state=localDraft;}
  function renderDraftStatus(){
    const draft=$("#draft-label"),pill=$("#sync-pill"),title=$("#draft-state-title"),detail=$("#draft-state-detail"),indicator=$("#dock-indicator");
    let label="GitHub 已同步",message="当前没有待同步的草稿。",className="success";
    if(state.browserDirty){label="浏览器修改未保存";message="修改还在当前页面内，尚未写入本机草稿。";className="warning";}
    else if(state.data?.dirty){label="本机草稿待同步";message="草稿已保存在 NAS 管理器，GitHub 仍是旧版本。";className="warning";}
    else if(state.data?.remote_status?.remote_changed){label="GitHub 有新版本";message="远端规则数据已有变化；重新载入前请先确认草稿状态。";className="warning";}
    draft.textContent=label;draft.className=`tag ${className}`;pill.textContent=label;pill.className=`status-pill ${className}`;title.textContent=label;detail.textContent=message;indicator.className=`dock-indicator ${className}`;
  }
  const activeTask=(kind)=>Boolean((state.data?.tasks||[]).some(task=>task.kind===kind&&["queued","running","polling","downloading"].includes(task.status)));
  function renderActionControls(){
    if(!state.data)return;
    const saveButton=$("#save-button"),buildButton=$("#build-button"),saveReason=$("#save-reason"),buildReason=$("#build-reason");
    const pendingDraft=state.browserDirty||Boolean(state.data.dirty),saveBusy=state.busy.save||activeTask("save"),buildBusy=state.busy.build||activeTask("build"),pipelineBusy=state.busy.deployment||["generate_prepare","prepare_deploy","manual_deploy","rollback"].some(activeTask);
    saveButton.disabled=saveBusy||buildBusy||pipelineBusy||!pendingDraft;saveButton.textContent=saveBusy?"保存中…":"保存到 GitHub";
    saveReason.textContent=pipelineBusy?"生成或目标操作期间暂不可另行保存。":saveBusy?"正在等待 GitHub 保存任务完成。":buildBusy?"构建运行期间暂不可保存。":pendingDraft?"点击后先保存本机草稿，再等待 GitHub 同步。":"没有待同步修改。";
    const buildBlock=state.browserDirty?"先同步浏览器内修改。":state.data.dirty?"先完成本机草稿的 GitHub 同步。":!state.data.synced_commit?"尚未读取到已同步的数据版本。":"";
    buildButton.disabled=Boolean(buildBlock)||saveBusy||buildBusy||pipelineBusy;buildButton.textContent=buildBusy?"构建运行中…":"发起构建";
    buildReason.textContent=pipelineBusy?"生成或目标操作运行期间暂不可另行构建。":buildBusy?"正在等待 Actions 和候选校验。":saveBusy?"保存完成后才能开始构建。":buildBlock||"基于当前已同步版本触发 GitHub Actions。";
    renderDraftStatus();
  }
  function setWorkspace(workspace,tab="services",targetId=null){
    const validWorkspaces=["rules","strategies","versions","deployment","records"].includes(workspace)?workspace:"rules";
    document.querySelectorAll(".workspace-view").forEach(view=>{view.hidden=view.dataset.workspace!==validWorkspaces;});
    document.querySelectorAll("[data-workspace-link]").forEach(link=>{const active=link.dataset.workspaceLink===validWorkspaces;link.classList.toggle("active",active);if(active)link.setAttribute("aria-current","page");else link.removeAttribute("aria-current");});
    $("#action-bar").hidden=validWorkspaces!=="rules";
    $("#page-title").textContent={rules:"服务与规则管理",strategies:"策略组控制",versions:"规则版本",deployment:"规则部署",records:"操作记录"}[validWorkspaces];
    if(validWorkspaces==="rules"){
      const validTab=["services","domains","mapping"].includes(tab)?tab:"services";
      document.querySelectorAll("[data-rules-tab]").forEach(panel=>{panel.hidden=panel.dataset.rulesTab!==validTab;});
      document.querySelectorAll("[data-rules-tab-link]").forEach(link=>{const active=link.dataset.rulesTabLink===validTab;link.classList.toggle("active",active);if(active)link.setAttribute("aria-current","page");else link.removeAttribute("aria-current");});
    }
    if(validWorkspaces==="strategies")ensureStrategyLoaded();
    if(targetId)requestAnimationFrame(()=>document.getElementById(targetId)?.scrollIntoView({block:"start",behavior:"smooth"}));
  }
  function syncWorkspaceFromHash(scrollToLegacy=false){
    const raw=decodeURIComponent(location.hash.slice(1));
    const legacy={"rules-editor":["rules","domains","rules-editor"],"service-catalog-panel":["rules","services","service-catalog-panel"],"candidate-panel":["versions","services","candidate-panel"],"deployment-panel":["deployment","services","deployment-panel"],history:["records","services","history"],"history-table":["records","services","history-table"],"categories-table":["rules","mapping","mapping-panel"]};
    if(legacy[raw]){const [workspace,tab,target]=legacy[raw];setWorkspace(workspace,tab,scrollToLegacy?target:null);return;}
    const [section,subsection]=raw.split("/");
    if(section==="versions")setWorkspace("versions");
    else if(section==="strategies")setWorkspace("strategies");
    else if(section==="deployment")setWorkspace("deployment");
    else if(section==="records")setWorkspace("records");
    else setWorkspace("rules",section==="rules"?subsection:"services");
  }
  function render(){
    renderRules();renderCategories();renderCatalog();renderTasks();renderReleases();renderDeployment();renderStrategies();
    const legacyRules=(state.data.state.rules||[]).filter(rule=>rule.category==="tiktok"&&rule.enabled===false);
    const compatibility=$("#compatibility-notice");compatibility.replaceChildren();
    (state.data.compatibility_warnings||[]).forEach(message=>{const p=document.createElement("p");p.textContent=message;compatibility.append(p);});
    if(legacyRules.length){const button=document.createElement("button");button.className="secondary compact";button.textContent="下载已停用的旧 TikTok 规则";button.addEventListener("click",()=>{const blob=new Blob([JSON.stringify({rules:legacyRules},null,2)],{type:"application/json"}),url=URL.createObjectURL(blob),link=document.createElement("a");link.href=url;link.download="legacy-disabled-tiktok-rules.json";link.click();URL.revokeObjectURL(url);});compatibility.append(button);}
    compatibility.hidden=!compatibility.childNodes.length;
    const remote=state.data.remote_status,remoteNode=$("#remote-check-status");
    if(remote?.status==="ok"){remoteNode.textContent=`远端检查 ${displayTime(remote.checked_at)} · ${remote.remote_changed?"rules-data 有新提交":"规则数据未变化"} · 最新规则 Release ${remote.latest_release_tag||"暂无"}`;}
    else if(remote?.status==="error"){remoteNode.textContent=`远端检查失败：${remote.error} · ${displayTime(remote.checked_at)}`;}
    renderDraftStatus();
    renderActionControls();syncWorkspaceFromHash();
  }
  const strategyRefKey=ref=>ref&&typeof ref==="object"?JSON.stringify({kind:ref.kind,name:ref.name,...(ref.provider?{provider:ref.provider}:{})}):"null";
  const strategyRefLabel=ref=>{
    if(!ref||typeof ref!=="object")return "缺少引用";
    if(ref.kind==="special")return ref.name==="REJECT"?"REJECT":"特殊项 · "+(ref.name||"未知");
    if(ref.kind==="provider")return `${ref.name||"未知 Provider"}${ref.provider?` · ${ref.provider}`:""}`;
    return ref.name||"未命名引用";
  };
  const strategyRefType=ref=>({group:"策略组",proxy:"代理节点",provider:"Provider",special:"特殊项"})[ref?.kind]||"未知类型";
  function currentStrategyGroup(){return state.strategy.data?.groups?.find(group=>group.name===state.strategy.groupName)||null;}
  function currentStrategyCatalogItem(ref){return (state.strategy.data?.catalog||[]).find(item=>strategyRefKey(item.ref)===strategyRefKey(ref))||null;}
  function strategyInspectAvailable(){
    const data=state.strategy.data;
    if(!data)return {enabled:false,reason:state.strategy.loading?"正在读取目标策略状态。":state.strategy.error||"策略状态尚未读取。"};
    if(data.busy_task)return {enabled:false,reason:`另一项目标操作正在运行${data.busy_task.kind?`（${data.busy_task.kind}）`:""}。请等待完成后刷新。`};
    if(state.strategy.busy)return {enabled:false,reason:"策略操作正在排队或执行，请等待任务结束。"};
    return {enabled:true,reason:""};
  }
  function strategyAvailable(){
    const data=state.strategy.data;
    const inspect=strategyInspectAvailable();
    if(!inspect.enabled)return inspect;
    if(data.maintenance_enabled!==true)return {enabled:false,reason:"目标维护禁用；策略组操作暂不可用。"};
    return {enabled:true,reason:""};
  }
  function ensureStrategyLoaded(){if(!state.strategy.data&&!state.strategy.loading)loadStrategies();}
  async function loadStrategies({silent=false}={}){
    if(state.strategy.loading)return;
    state.strategy.previewSerial++;state.strategy.previewing=false;state.strategy.preview=null;
    state.strategy.loading=true;if(!silent)state.strategy.error=null;renderStrategyStatus();
    try{
      const incoming=await request("/api/strategies");
      if(!Array.isArray(incoming.groups)||!Array.isArray(incoming.catalog))throw new Error("策略响应缺少 groups 或 catalog 列表");
      const previousName=state.strategy.groupName;
      state.strategy.data=incoming;state.strategy.error=null;
      const picked=incoming.groups.find(group=>group.name===previousName)||incoming.groups.find(group=>group.managed!==false)||incoming.groups[0]||null;
      state.strategy.groupName=picked?.name||null;
      const current=currentStrategyGroup();
      state.strategy.editorMembers=new Set((current?.members||[]).map(strategyRefKey));
      state.strategy.selectedChoice=current?.selected?strategyRefKey(current.selected):null;
      state.strategy.draftSelectedChoice=current?.selected?strategyRefKey(current.selected):null;
      state.strategy.preview=null;
      if(incoming.busy_task)watchStrategyTask(incoming.busy_task.task_id||incoming.busy_task.id);
    }catch(error){
      if(error.details?.busy_task){
        state.strategy.error=null;state.strategy.data={...(state.strategy.data||{}),busy_task:error.details.busy_task};
        state.strategy.taskMessage=`目标正执行 ${error.details.busy_task.kind||"其他"} 任务；保留当前目录和编辑，完成后自动刷新。`;
        watchStrategyTask(error.details.busy_task.task_id||error.details.busy_task.id);
      }else state.strategy.error=error.message;
    }
    finally{state.strategy.loading=false;renderStrategies();}
  }
  function renderStrategyStatus(){
    const status=$("#strategy-task-status");
    if(state.strategy.error){status.hidden=false;status.className="strategy-task-status error";status.textContent=state.strategy.error;return;}
    const info=strategyAvailable();
    const count=state.strategy.data?.groups?.filter(group=>group.managed!==false).length||0;
    if(state.strategy.busy||state.strategy.data?.busy_task){status.hidden=false;status.className="strategy-task-status";status.textContent=state.strategy.taskMessage||info.reason||`已读取 ${count} 个受管策略组。`;return;}
    if(state.strategy.data&&state.strategy.data.maintenance_enabled!==true){status.hidden=false;status.className="strategy-task-status";status.textContent="维护已禁用：仍可查看、编辑和预览成员草稿；立即选择与应用操作暂不可用。";return;}
    status.hidden=true;status.textContent="";
  }
  function renderStrategyGroups(){
    const host=$("#strategy-group-list"),count=$("#strategy-group-count");host.replaceChildren();
    const groups=state.strategy.data?.groups||[],query=$("#strategy-group-search").value.trim().toLowerCase();
    const managed=groups.filter(group=>group.managed!==false);
    count.textContent=`${managed.length} 个受管组`;
    const visible=groups.filter(group=>!query||String(group.name).toLowerCase().includes(query));
    visible.forEach(group=>{
      const button=document.createElement("button");button.type="button";button.className="strategy-group-option";button.setAttribute("aria-pressed",String(group.name===state.strategy.groupName));button.disabled=state.strategy.busy;
      const name=document.createElement("span");name.textContent=group.name;
      const meta=document.createElement("small");meta.textContent=group.managed===false?"只读引用":`${(group.members||[]).length} 项`;
      button.append(name,meta);button.addEventListener("click",()=>selectStrategyGroup(group.name));host.append(button);
    });
    if(!visible.length){const empty=document.createElement("p");empty.className="empty-state";empty.textContent=groups.length?"没有匹配的策略组。":"没有受管 select 组。";host.append(empty);}
  }
  function selectStrategyGroup(name){
    const group=state.strategy.data?.groups?.find(item=>item.name===name);if(!group)return;
    state.strategy.groupName=name;state.strategy.editorMembers=new Set((group.members||[]).map(strategyRefKey));state.strategy.selectedChoice=group.selected?strategyRefKey(group.selected):null;state.strategy.draftSelectedChoice=group.selected?strategyRefKey(group.selected):null;state.strategy.preview=null;state.strategy.previewSerial++;state.strategy.previewing=false;renderStrategies();
  }
  function strategyDraftChanged(){
    const group=currentStrategyGroup();if(!group)return false;
    const saved=new Set((group.members||[]).map(strategyRefKey));
    return saved.size!==state.strategy.editorMembers.size||[...saved].some(key=>!state.strategy.editorMembers.has(key))||state.strategy.draftSelectedChoice!==(group.selected?strategyRefKey(group.selected):null);
  }
  function renderStrategyCurrentMembers(group,available){
    const host=$("#strategy-current-members");host.replaceChildren();
    const refs=[...(group.members||[])];
    if(group.selected&&!refs.some(ref=>strategyRefKey(ref)===strategyRefKey(group.selected)))refs.unshift(group.selected);
    const unique=[...new Map(refs.map(ref=>[strategyRefKey(ref),ref])).values()];
    if(!unique.length){const empty=document.createElement("p");empty.className="empty-state";empty.textContent="此组没有可立即选择的成员。先编辑并应用成员列表。";host.append(empty);return;}
    unique.forEach((ref,index)=>{
      const item=currentStrategyCatalogItem(ref),missing=!item,label=document.createElement("label");label.className="strategy-radio-row";
      const radio=document.createElement("input");radio.type="radio";radio.name="strategy-current-choice";radio.value=strategyRefKey(ref);radio.checked=state.strategy.selectedChoice===radio.value;radio.disabled=!available.enabled||group.managed===false||missing||item.selectable===false||!(group.members||[]).some(member=>strategyRefKey(member)===radio.value);radio.setAttribute("aria-label",`选择 ${strategyRefLabel(ref)}`);
      radio.addEventListener("change",()=>{state.strategy.selectedChoice=radio.value;$("#strategy-select-save").disabled=!strategyAvailable().enabled||radio.value===strategyRefKey(group.selected);$("#strategy-select-reason").textContent=radio.value===strategyRefKey(group.selected)?"这已是保存偏好。":"确认后将立即切换并保存此选择。";});
      const copy=document.createElement("span");copy.className="strategy-choice-copy";const strong=document.createElement("strong");strong.textContent=strategyRefLabel(ref);const small=document.createElement("small");small.textContent=`${strategyRefType(ref)}${ref.kind==="provider"&&ref.provider?` · 来源 ${ref.provider}`:""}`;copy.append(strong,small);if(missing||item?.warning||item?.selectable===false){const warning=document.createElement("span");warning.className="strategy-warning-inline";warning.textContent=missing?"目录中未找到此成员引用。":item.warning||"此选项当前不可选择。";copy.append(warning);}label.append(radio,copy);host.append(label);
    });
  }
  function renderStrategyCatalog(group,available){
    const host=$("#strategy-catalog-list");host.replaceChildren();
    const query=$("#strategy-catalog-search").value.trim().toLowerCase(),items=state.strategy.data?.catalog||[];
    const visible=items.filter(item=>{
      const ref=item.ref||{},haystack=`${ref.name||""} ${ref.provider||""} ${ref.kind||""} ${strategyRefType(ref)} ${item.name||""}`.toLowerCase();
      return !query||haystack.includes(query);
    });
    visible.forEach((item,index)=>{
      const ref=item.ref;if(!ref)return;const key=strategyRefKey(ref),selectable=item.selectable===true,checked=state.strategy.editorMembers.has(key);
      const editable=Boolean(strategyInspectAvailable().enabled&&group?.managed!==false);
      const row=document.createElement("div");row.className="strategy-catalog-option";row.setAttribute("aria-disabled",String(!editable||(!selectable&&!checked)));
      const checkbox=document.createElement("input");checkbox.id=`strategy-member-${index}`;checkbox.type="checkbox";checkbox.checked=checked;checkbox.disabled=!editable||(!selectable&&!checked);checkbox.setAttribute("aria-label",`${checked?"保留":"添加"} ${strategyRefLabel(ref)}，${strategyRefType(ref)}`);
      checkbox.addEventListener("change",()=>{checkbox.checked?state.strategy.editorMembers.add(key):state.strategy.editorMembers.delete(key);if(!checkbox.checked&&state.strategy.draftSelectedChoice===key)state.strategy.draftSelectedChoice=null;state.strategy.preview=null;state.strategy.previewSerial++;state.strategy.previewing=false;renderStrategies();});
      const copy=document.createElement("span");copy.className="strategy-choice-copy";const title=document.createElement("strong");title.textContent=strategyRefLabel(ref);const meta=document.createElement("small");meta.textContent=`${strategyRefType(ref)}${ref.provider?` · ${ref.provider}`:""}`;copy.append(title,meta);
      if(!selectable||item.warning||item.warnings?.length){const warning=document.createElement("span");warning.className="strategy-warning-inline";warning.textContent=item.warning||item.warnings?.join("；")||"此选项不可加入策略组";copy.append(warning);}
      const checkLabel=document.createElement("label");checkLabel.htmlFor=checkbox.id;checkLabel.className="strategy-catalog-member-label";checkLabel.append(copy);
      const draftRadio=document.createElement("input");draftRadio.id=`strategy-choice-${index}`;draftRadio.type="radio";draftRadio.name="strategy-draft-choice";draftRadio.checked=state.strategy.draftSelectedChoice===key;draftRadio.disabled=!selectable||!editable||!checked;draftRadio.setAttribute("aria-label",`成员应用后选择 ${strategyRefLabel(ref)}`);draftRadio.title="应用成员列表时设为此组选择";
      draftRadio.addEventListener("change",()=>{state.strategy.draftSelectedChoice=key;state.strategy.preview=null;state.strategy.previewSerial++;state.strategy.previewing=false;renderStrategies();});
      const draftLabel=document.createElement("label");draftLabel.className="strategy-draft-radio-label";draftLabel.htmlFor=draftRadio.id;draftLabel.append(draftRadio,document.createTextNode("应用后选择"));row.append(checkbox,checkLabel,draftLabel);host.append(row);
    });
    const missing=[...(group?.members||[])].filter(ref=>!currentStrategyCatalogItem(ref));
    missing.forEach(ref=>{
      const key=strategyRefKey(ref),label=document.createElement("label");label.className="strategy-catalog-option";label.setAttribute("aria-disabled","false");
      const checkbox=document.createElement("input");checkbox.type="checkbox";checkbox.checked=state.strategy.editorMembers.has(key);checkbox.disabled=!strategyInspectAvailable().enabled||group?.managed===false;checkbox.setAttribute("aria-label",`移除目录中缺失的成员 ${strategyRefLabel(ref)}`);
      checkbox.addEventListener("change",()=>{checkbox.checked?state.strategy.editorMembers.add(key):state.strategy.editorMembers.delete(key);if(!checkbox.checked&&state.strategy.draftSelectedChoice===key)state.strategy.draftSelectedChoice=null;state.strategy.preview=null;state.strategy.previewSerial++;state.strategy.previewing=false;renderStrategies();});
      const copy=document.createElement("span");copy.className="strategy-choice-copy";const title=document.createElement("strong");title.textContent=`${strategyRefLabel(ref)} · 目录缺失`;const warning=document.createElement("small");warning.className="strategy-warning-inline";warning.textContent="可以从成员草稿中移除此失效引用。";copy.append(title,warning);label.append(checkbox,copy);host.append(label);
    });
    if(!visible.length){const empty=document.createElement("p");empty.className="empty-state";empty.textContent=items.length?"没有匹配的节点、策略组或 Provider。":"目标没有返回可用目录。";host.append(empty);}
  }
  function renderStrategyWarnings(group){
    const box=$("#strategy-warnings");box.replaceChildren();const warnings=Array.isArray(group?.warnings)?[...group.warnings]:[];
    const selected=group?.selected,selectedItem=selected?currentStrategyCatalogItem(selected):null;
    if(selected&&!selectedItem)warnings.push(`保存偏好 ${strategyRefLabel(selected)} 不在当前目录中。`);
    if(selectedItem?.selectable===false)warnings.push(selectedItem.warning||`${strategyRefLabel(selected)} 当前不可选择。`);
    if(group?.actual&&selected&&group.actual!==selected.name)warnings.push(`运行实际选择为 ${group.actual}，与保存偏好 ${selected.name} 不一致。`);
    warnings.forEach(value=>{const row=document.createElement("div");row.textContent=typeof value==="string"?value:JSON.stringify(value);box.append(row);});
    box.hidden=!warnings.length;
  }
  function renderStrategyEditState(){
    const group=currentStrategyGroup(),available=strategyAvailable(),inspect=strategyInspectAvailable(),canEdit=Boolean(group?.managed!==false&&inspect.enabled),currentKeys=new Set((group?.members||[]).map(strategyRefKey));
    const changed=state.strategy.editorMembers.size!==currentKeys.size||[...state.strategy.editorMembers].some(key=>!currentKeys.has(key));
    const selected=group?.selected,draftSelected=state.strategy.draftSelectedChoice,selectionStillMember=Boolean(draftSelected&&state.strategy.editorMembers.has(draftSelected));
    $("#strategy-member-count").textContent=`${state.strategy.editorMembers.size} 个成员`;
    $("#strategy-preview").disabled=!canEdit||!changed||!selectionStillMember||state.strategy.busy||state.strategy.previewing;
    $("#strategy-preview").textContent=state.strategy.previewing?"正在预览…":"预览成员修改";
    $("#strategy-apply").disabled=!available.enabled||group?.managed===false||!state.strategy.preview?.ok||!/^([0-9a-f]{64})$/.test(state.strategy.preview?.preview_sha256||"")||state.strategy.busy||state.strategy.previewing;
    $("#strategy-edit-reason").textContent=group?.managed===false?"受保护组只能作为其他策略组的引用，不能编辑成员。":!inspect.enabled?inspect.reason:!changed?"勾选或取消候选成员后，可预览修改。":!selectionStillMember?"请选择一个仍保留在成员草稿中的选项作为应用后的选择。":!available.enabled?`${available.reason}仍可编辑并预览，应用操作暂不可用。`:"预览将同时检查成员列表及应用后的选择。";
    $("#strategy-select-save").disabled=group?.managed===false||!available.enabled||!state.strategy.selectedChoice||state.strategy.selectedChoice===strategyRefKey(selected)||state.strategy.busy;
    $("#strategy-select-reason").textContent=group?.managed===false?"受保护组只读。":!available.enabled?available.reason:"选择列表中的现有成员后，确认即可立即切换并保存。";
    const previewBox=$("#strategy-preview-result");previewBox.hidden=!state.strategy.preview;previewBox.replaceChildren();
    if(state.strategy.preview){
      const preview=state.strategy.preview,title=document.createElement("p");title.textContent=preview.ok?`预览通过 · revision ${preview.revision} · ${preview.changes?.length??"已校验"} 项变更`:`预览受阻 · ${preview.error||preview.reason||"目标拒绝此成员列表"}`;previewBox.append(title);
      const details=[...(preview.warnings||[]),...(preview.blockers||[])];if(preview.error||preview.reason||preview.local_error)details.unshift(preview.error||preview.reason||preview.local_error);
      if(details.length){const list=document.createElement("ul");details.forEach(value=>{const li=document.createElement("li");li.className="warning-copy";li.textContent=typeof value==="string"?value:JSON.stringify(value);list.append(li);});previewBox.append(list);}
      const changes=preview.changes||[];if(changes.length){const list=document.createElement("ul");changes.forEach(value=>{const li=document.createElement("li");li.textContent=typeof value==="string"?value:JSON.stringify(value);list.append(li);});previewBox.append(list);}
      if(!preview.ok&&!(preview.warnings?.length||preview.blockers?.length||preview.error||preview.reason)){const p=document.createElement("p");p.className="warning-copy";p.textContent="后端预览未批准此成员列表。";previewBox.append(p);}
    }
  }
  function renderStrategies(){
    renderStrategyStatus();renderStrategyGroups();const data=state.strategy.data,group=currentStrategyGroup();
    $("#strategy-target").textContent=data?.target_id?`主目标 ${data.target_id}`:"主目标未配置";
    const maintenance=$("#strategy-maintenance");maintenance.textContent=data?.maintenance_enabled===true?"维护已启用":"维护禁用";maintenance.className="tag "+(data?.maintenance_enabled===true?"success":"warning");
    $("#strategy-detail").hidden=!group;$("#strategy-empty").hidden=Boolean(group);$("#strategy-empty").textContent=state.strategy.loading?"正在读取目标策略状态…":state.strategy.error||"请选择一个策略组。";
    if(!group){return;}
    $("#strategy-name").textContent=group.name;$("#strategy-managed-tag").textContent=group.managed===false?"受保护，只读引用":"受管 select 组";$("#strategy-managed-tag").className="tag "+(group.managed===false?"neutral":"success");
    const selected=group.selected,actual=group.actual,selectedNode=$("#strategy-selected"),actualNode=$("#strategy-actual");
    selectedNode.textContent=selected?`${strategyRefLabel(selected)} · ${strategyRefType(selected)}`:"尚未保存选择";actualNode.textContent=actual||"目标没有报告实际选择";
    selectedNode.classList.toggle("mismatch",Boolean(selected&&actual&&actual!==selected.name));actualNode.classList.toggle("mismatch",Boolean(selected&&actual&&actual!==selected.name));
    renderStrategyWarnings(group);renderStrategyCurrentMembers(group,strategyAvailable());renderStrategyCatalog(group,strategyAvailable());renderStrategyEditState();
  }
  function invalidateStrategyPreview(){state.strategy.preview=null;renderStrategyEditState();}
  async function submitStrategyOperation(endpoint,payload,label){
    if(!strategyAvailable().enabled){notice(strategyAvailable().reason,true);return;}
    state.strategy.busy=true;state.strategy.error=null;state.strategy.taskMessage=`正在提交${label}…`;renderStrategies();
    try{
      const result=await request(endpoint,{method:"POST",body:JSON.stringify(payload)});
      if(result?.task_id){state.strategy.operation=result.operation||endpoint.split("/").at(-1);state.strategy.taskId=result.task_id;state.strategy.taskMessage=`${label}已排队，等待目标操作完成。`;watchStrategyTask(result.task_id);}
      else{state.strategy.busy=false;state.strategy.taskMessage=`${label}已完成。`;await loadStrategies({silent:true});notice(`${label}已完成。`);}
      renderStrategies();
    }catch(error){state.strategy.busy=false;state.strategy.error=error.message;state.strategy.taskMessage="";renderStrategies();notice(error.message,true);}
  }
  function watchStrategyTask(taskId){
    if(!taskId||state.strategy.taskTimer)return;
    state.strategy.taskId=taskId;state.strategy.busy=true;
    const poll=async()=>{
      try{
        const result=await request("/api/tasks"),task=(result.tasks||[]).find(row=>row.id===taskId);
        if(!task){state.strategy.taskMessage="策略任务仍在队列中；等待任务状态更新。";return;}
        if(task.kind==="strategy_select")state.strategy.operation="select";else if(task.kind==="strategy_apply")state.strategy.operation="apply";
        const active=["queued","running","polling","downloading"].includes(task.status);state.strategy.taskMessage=`${task.status==="queued"?"排队中":task.status==="running"?"执行中":task.status==="polling"?"等待目标响应":"下载校验"} · ${task.message||"目标任务"}`;
        if(active){renderStrategyStatus();return;}
        clearInterval(state.strategy.taskTimer);state.strategy.taskTimer=null;state.strategy.busy=false;state.strategy.taskId=null;
        if(task.status==="succeeded"){
          state.strategy.taskMessage=state.strategy.operation==="select"?"策略选择已完成；正在读取目标实际状态。":state.strategy.operation==="apply"?"策略成员应用已完成；正在读取目标实际状态。":"目标任务已完成；正在刷新策略状态。";
          if(state.strategy.operation==="apply")state.strategy.preview=null;
          state.strategy.operation=null;
          await loadStrategies({silent:true});notice("策略目标操作已完成，状态已刷新。");
        }else{state.strategy.error=task.message||`策略操作${task.status}。`;state.strategy.taskMessage="";notice(state.strategy.error,true);}
        renderStrategies();
      }catch(error){state.strategy.taskMessage=`正在等待策略任务状态：${error.message}`;renderStrategyStatus();}
    };
    poll();state.strategy.taskTimer=setInterval(poll,1800);renderStrategyStatus();
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
      if(rule.category==="tiktok"){const option=document.createElement("option");option.value="tiktok";option.textContent="TikTok · 已移除（只保留停用旧规则）";option.selected=true;option.disabled=true;select.append(option);}
      select.addEventListener("change",()=>{rule.category=select.value;dirty();});category.append(select);row.append(category);
      const enabled=document.createElement("td"),toggle=document.createElement("button");toggle.className="quiet";toggle.textContent=rule.enabled===false?"已停用":"启用中";toggle.setAttribute("aria-pressed",String(rule.enabled!==false));toggle.addEventListener("click",()=>{rule.enabled=rule.enabled===false;dirty();renderRules();});enabled.append(toggle);row.append(enabled);
      const actions=document.createElement("td"),edit=document.createElement("button"),remove=document.createElement("button");edit.className="quiet";edit.textContent="编辑";edit.addEventListener("click",()=>{state.editing=index;$("#rule-domain").value=rule.domain;$("#rule-match").value=rule.match_type;$("#rule-category").value=rule.category;$("#rule-note").value=rule.note||"";$("#rule-add").textContent="保存修改";$("#rule-domain").focus();});remove.className="quiet";remove.textContent="删除";remove.addEventListener("click",()=>{state.data.state.rules.splice(index,1);dirty();renderRules();});actions.append(edit,remove);row.append(actions);body.append(row);
    });
    if(!visible.length){const row=document.createElement("tr"),cell=document.createElement("td");cell.colSpan=5;cell.className="empty";cell.textContent=state.data.state.rules.length?"没有符合筛选条件的规则。":"没有自定义规则。添加一条域名规则开始。";row.append(cell);body.append(row);}
  }
  function renderCategories(){
    const body=$("#categories-table");body.replaceChildren();
    const query=$("#mapping-search").value.trim().toLowerCase();
    Object.entries(state.categories).filter(([id,base])=>{const cfg=effectiveCategory(id);return !query||`${id} ${base.label} ${cfg.policy_group}`.toLowerCase().includes(query);}).forEach(([id,base])=>{
      const cfg=effectiveCategory(id),row=document.createElement("tr");
      const name=document.createElement("td"),bold=document.createElement("strong");bold.textContent=base.label;name.append(bold);const small=document.createElement("small");small.textContent=` ${id}`;small.className="muted";name.append(small);row.append(name);
      const service=(state.data.service_catalog||[]).find(item=>item.category_id===id||item.category_ids?.includes(id)),blocked=service&&catalogReadOnly(service);
      const on=document.createElement("td"),check=document.createElement("input");check.type="checkbox";check.checked=cfg.enabled!==false;check.disabled=Boolean(blocked);check.setAttribute("aria-label",`${base.label} 规则启用`);check.addEventListener("change",()=>{state.data.state.categories[id]={...state.data.state.categories[id],enabled:check.checked};dirty();renderCatalog();});on.append(check);if(blocked){const why=document.createElement("small");why.className="muted";why.textContent="目录只读";on.append(why);}else if(service?.review_state!=="approved"){const why=document.createElement("small");why.className="muted";why.textContent="待审核；构建后仍受部署门禁限制";on.append(why);}row.append(on);
      const policy=document.createElement("td"),select=document.createElement("select");select.setAttribute("aria-label",`${base.label} 策略组`);state.data.policies.forEach(item=>{const option=document.createElement("option");option.value=item;option.textContent=item;option.selected=item===cfg.policy_group;select.append(option);});select.addEventListener("change",()=>{state.data.state.categories[id]={...state.data.state.categories[id],policy_group:select.value};dirty();renderCatalog();});policy.append(select);row.append(policy);
      const priority=document.createElement("td"),number=document.createElement("input");number.type="number";number.className="priority-input";number.value=cfg.priority;number.setAttribute("aria-label",`${base.label} 优先级`);number.addEventListener("change",()=>{state.data.state.categories[id]={...state.data.state.categories[id],priority:Number(number.value)};dirty();});priority.append(number);row.append(priority);
      const source=document.createElement("td"),details=document.createElement("details"),summary=document.createElement("summary");summary.textContent="查看";const code=document.createElement("code"),refs=Array.isArray(base.source_refs)?base.source_refs:base.source_file?[{source_id:"blackmatrix7",file:base.source_file}]:[];code.textContent=refs.length?refs.map(ref=>`${ref.source_id}:${ref.key||ref.file}`).join("\n"):"自定义";details.append(summary,code);source.append(details);row.append(source);body.append(row);
    });
  }
  function renderCatalog(){
    const body=$("#catalog-table");body.replaceChildren();
    const all=state.data.service_catalog||[],group=$("#catalog-group-filter").value,status=$("#catalog-status-filter").value,query=$("#catalog-search").value.trim().toLowerCase();
    const groups=[...new Set(all.map(item=>item.use_group).filter(Boolean))].sort();
    if(!state.catalogGroupsInitialized){groups.forEach(value=>{const option=document.createElement("option");option.value=value;option.textContent=value;$("#catalog-group-filter").append(option);});state.catalogGroupsInitialized=true;}
    const shown=all.filter(item=>{const text=(item.id+" "+item.label).toLowerCase(),readonly=catalogReadOnly(item),category=item.category_id?state.data.categories[item.category_id]:null,cfg=item.category_id?effectiveCategory(item.category_id):null;let stateMatch=!status;if(status==="read-only")stateMatch=readonly;else if(status==="enabled")stateMatch=Boolean(category)&&cfg.enabled!==false;else if(status==="disabled")stateMatch=Boolean(category)&&cfg.enabled===false;else if(status)stateMatch=item.review_state===status;return (!query||text.includes(query))&&(!group||item.use_group===group)&&stateMatch;});
    $("#catalog-count").textContent=shown.length+" / "+all.length+" 项";
    shown.forEach(item=>{
      const row=document.createElement("tr"),category=item.category_id?state.data.categories[item.category_id]:null,cfg=item.category_id?effectiveCategory(item.category_id):null;
      const readonly=catalogReadOnly(item);
      const pick=document.createElement("td");pick.dataset.label="选择";if(item.category_id&&!readonly){const checkbox=document.createElement("input");checkbox.type="checkbox";checkbox.checked=state.catalogSelected.has(item.id);checkbox.setAttribute("aria-label","批量选择 "+item.label);checkbox.addEventListener("change",()=>{checkbox.checked?state.catalogSelected.add(item.id):state.catalogSelected.delete(item.id);invalidateBatchPreview();updateCatalogSelection();});pick.append(checkbox);}else pick.textContent="—";row.append(pick);
      const label=document.createElement("td"),strong=document.createElement("strong"),id=document.createElement("small");strong.textContent=item.label;id.textContent=item.id;id.className="muted mono";label.append(strong,id);label.dataset.label="服务";row.append(label);
      const purpose=document.createElement("td");purpose.dataset.label="用途";purpose.textContent=item.use_group||"—";row.append(purpose);
      const running=document.createElement("td");running.dataset.label="规则启用 / 策略组";if(category&&!readonly){const toggleLabel=document.createElement("label");toggleLabel.className="service-toggle-label";const toggle=document.createElement("input");toggle.type="checkbox";toggle.checked=cfg.enabled!==false;toggle.setAttribute("aria-label",`${item.label} 规则启用`);toggle.addEventListener("change",()=>{state.data.state.categories[item.category_id]={...state.data.state.categories[item.category_id],enabled:toggle.checked};dirty();renderCategories();renderCatalog();});const toggleText=document.createElement("span");toggleText.textContent=cfg.enabled!==false?"规则已启用":"规则已停用";toggleLabel.append(toggle,toggleText);running.append(toggleLabel);const policy=document.createElement("select");policy.className="service-policy";policy.setAttribute("aria-label",`${item.label} 策略组`);state.policies.forEach(name=>{const option=document.createElement("option");option.value=name;option.textContent=name;option.selected=name===cfg.policy_group;policy.append(option);});policy.addEventListener("change",()=>{state.data.state.categories[item.category_id]={...state.data.state.categories[item.category_id],policy_group:policy.value};dirty();renderCategories();renderCatalog();});running.append(policy);}else running.textContent=readonly?"只读参考":"未配置运行类别";row.append(running);
      const detailCell=document.createElement("td");detailCell.dataset.label="审核与详情";const details=document.createElement("details"),summary=document.createElement("summary"),content=document.createElement("div");summary.textContent="审核 / 覆盖 / 来源";const tag=document.createElement("span");tag.className="tag "+(item.review_state==="approved"?"success":"warning");tag.textContent=item.review_state==="approved"?"已审核":item.review_state==="manual_missing"?"缺来源":"待审核";content.append(tag);const coverage=document.createElement("p");coverage.textContent="覆盖："+(item.coverage_status||"未核");content.append(coverage);const ref=item.selected_ref,source=document.createElement("p");source.textContent="来源："+(ref?ref.source_id+" · "+ref.file:"无可选来源");content.append(source);if(item.dependencies?.length){const p=document.createElement("p");p.textContent="依赖："+item.dependencies.join("、");content.append(p);}if(item.risks?.length){const p=document.createElement("p");p.textContent="风险："+item.risks.join("、");content.append(p);}if(item.evidence?.length){const p=document.createElement("p");p.textContent="证据："+item.evidence.map(row=>typeof row==="string"?row:row.url||row.claim||"").filter(Boolean).slice(0,3).join("；");content.append(p);}details.append(summary,content);detailCell.append(details);row.append(detailCell);body.append(row);
    });
    if(!shown.length){const row=document.createElement("tr"),cell=document.createElement("td");cell.colSpan=5;cell.className="empty";cell.textContent="没有符合筛选条件的服务。";row.append(cell);body.append(row);}
    updateCatalogSelection();
  }
  function updateCatalogSelection(){$("#catalog-selection-count").textContent=state.catalogSelected.size?"已选 "+state.catalogSelected.size+" 项":"未选择服务";$("#catalog-actions").hidden=!state.catalogSelected.size;}
  async function previewCategoryBatch(action){if(!state.catalogSelected.size){notice("请先选择服务。",true);return;}const requestSequence=++state.previewSequence,revision=state.editRevision,ids=[...state.catalogSelected];state.batchAction=null;state.batchPreviewRevision=null;$("#batch-apply").disabled=true;const out=$("#batch-result");out.hidden=false;out.textContent="正在保存本机草稿并校验批量修改…";if(state.browserDirty){const snapshot=structuredClone(state.data.state);await request("/api/draft",{method:"PUT",body:JSON.stringify({state:snapshot})});if(requestSequence!==state.previewSequence||revision!==state.editRevision)return;state.browserDirty=false;state.data.dirty=true;renderActionControls();}const result=await request("/api/categories/batch-preview",{method:"POST",body:JSON.stringify({action,service_ids:ids})});if(requestSequence!==state.previewSequence||revision!==state.editRevision||ids.length!==state.catalogSelected.size||ids.some(id=>!state.catalogSelected.has(id)))return;state.batchAction=action;state.batchPreviewRevision=revision;state.batchPreviewIds=ids;const changes=result.changes.map(item=>item.service_id+"："+(item.before?"启用":"停用")+" → "+(item.enabled?"启用":"停用")),errors=result.blockers.map(item=>item.service_id+"："+item.reason);out.hidden=false;out.textContent=(result.ok?"预览通过":"预览受阻")+" · "+changes.length+" 项可修改\n"+[...errors,...changes].join("\n");$("#batch-apply").disabled=!result.ok;}
  function renderTasks(){
    const tasks=state.data.tasks||[];const list=$("#task-list"),history=$("#history-table");list.replaceChildren();history.replaceChildren();
    if(!tasks.length){list.innerHTML='<p class="empty-state">暂时没有任务。</p>';history.innerHTML='<p class="empty-state">等待载入。</p>';return;}
    tasks.slice(0,8).forEach(task=>{const row=document.createElement("div");row.className=`task-row ${task.status}`;const title=document.createElement("strong");title.textContent=`${taskLabel(task.kind)} · ${taskStatus(task)}`;const time=document.createElement("small");time.textContent=displayTime(task.updated_at);const msg=document.createElement("p");msg.textContent=taskMessage(task);row.append(title,time,msg);list.append(row);});
    const table=document.createElement("table");table.innerHTML="<thead><tr><th>时间</th><th>操作</th><th>状态</th><th>记录</th></tr></thead>";const tbody=document.createElement("tbody");tasks.forEach(task=>{const row=document.createElement("tr");[displayTime(task.updated_at),taskLabel(task.kind),taskStatus(task),taskMessage(task)].forEach(value=>{const cell=document.createElement("td");cell.textContent=value;row.append(cell);});tbody.append(row);});table.append(tbody);history.append(table);
  }
  function taskStatus(task){const phase=task.payload?.application_result?.phase;if(phase&&({auto_deployed:"已自动部署",approval_required:"等待批准",unchanged:"无需更新"})[phase])return ({auto_deployed:"已自动部署",approval_required:"等待批准",unchanged:"无需更新"})[phase];if(task.status==="blocked")return "检查未通过";if(task.status==="failed"){if(["manual_deploy","rollback"].includes(task.kind)||/部署失败或已回退/.test(task.message||""))return "部署失败或已回退";if(["generate_prepare","prepare_deploy"].includes(task.kind))return /检查未通过|准备未通过|验证失败|门禁阻止/.test(task.message||"")?"检查未通过":"处理失败";return task.kind==="build"?"构建失败":task.kind==="save"?"同步失败":"操作失败";}return statusText(task.status);}
  function taskMessage(task){const result=task.payload?.application_result,decision=task.payload?.deployment_decision,reasons=decision?.reasons;return result?[result.reason,...(result.deployment_decision?.reasons||[])].filter(Boolean).join("；"):decision?`${task.message}${Array.isArray(reasons)&&reasons.length?`；${reasons.join("；")}`:""}`:task.message;}
  function taskLabel(kind){return ({save:"保存规则",build:"生成规则",generate_prepare:"生成并应用",prepare_deploy:"准备并验证目标",manual_deploy:"人工部署",rollback:"人工回退"})[kind]||kind;}
  function statusText(value){return ({queued:"排队中",running:"运行中",polling:"等待 CI",downloading:"下载校验",succeeded:"已完成",failed:"失败",blocked:"受阻",interrupted:"中断"})[value]||value;}
  function renderDeployment(){
    const d=state.data.deployment||{},switchPill=$("#deployment-switch"),actual=$("#deployment-target-status"),prepSelect=$("#prepared-request"),rollbackSelect=$("#rollback-request"),generateButton=$("#generate-prepare"),reprepareButton=$("#reprepare-deployment"),deployButton=$("#manual-deployment"),rollbackButton=$("#rollback-deployment"),records=$("#deployment-records"),candidate=state.data.candidate_release_id;
    const expiryTime=p=>{const value=Date.parse(p.expires_at||"");return Number.isFinite(value)?value:0;};
    switchPill.textContent=d.configuration_error?"目标未配置":d.deployment_enabled?(d.automatic_deployment_enabled?"常规更新自动部署":"自动部署未启用"):"维护禁用";switchPill.className="tag "+(d.deployment_enabled?"success":"warning");
    const observed=d.actual_status;actual.textContent=observed?`最近目标读回 ${displayTime(observed.observed_at)} · ${Object.entries(observed.targets||{}).map(([id,rid])=>id+"="+(rid==="unknown"?"未知":rid==null?"尚无运行版本":String(rid).slice(0,12))).join(" · ")||observed.error||"无状态"}`:(d.target_ids?.length?"尚无实际目标读回记录。":"target 未配置");
    const priorPrep=prepSelect.value,priorRollback=rollbackSelect.value;prepSelect.replaceChildren();const valid=(d.preparations||[]).filter(p=>p.phase==="validated"&&p.release_id&&p.request_id&&!p.deployment_phase&&(!p.application_result?.phase||p.application_result.phase==="approval_required")).sort((a,b)=>String(b.task_updated_at||"").localeCompare(String(a.task_updated_at||""))||expiryTime(b)-expiryTime(a));if(!valid.length){const option=document.createElement("option");option.value="";option.textContent="暂无待批准版本";prepSelect.append(option);}else valid.forEach((p,index)=>{const option=document.createElement("option");option.value=p.request_id;const expires=p.expires_at?displayTime(p.expires_at):"有效期未知";option.textContent=`${p.release_id.slice(0,12)} · ${expires}`;option.selected=p.request_id===state.preferredPreparationRequest||(!state.preferredPreparationRequest&&p.request_id===priorPrep)||(!state.preferredPreparationRequest&&!valid.some(row=>row.request_id===priorPrep)&&index===0);prepSelect.append(option);});
    const selected=valid.find(p=>p.request_id===prepSelect.value)||valid[0],attExpiry=selected?Date.parse(selected.expires_at||""):NaN,attestationValid=Number.isFinite(attExpiry)&&attExpiry>Date.now();
    const generateReason=$("#generate-reason"),manualReason=$("#manual-reason"),rollbackReason=$("#rollback-reason");
    const busyKinds=["save","build","generate_prepare","prepare_deploy","manual_deploy","rollback"],deploymentBusy=state.busy.deployment||busyKinds.some(activeTask),activeUpdate=(state.data.tasks||[]).find(t=>["queued","running","polling","downloading"].includes(t.status)&&busyKinds.includes(t.kind));
    generateButton.disabled=!d.configured||deploymentBusy;generateButton.textContent=state.busy.deployment?"处理中…":"生成并应用";generateReason.textContent=deploymentBusy?`${activeUpdate?taskLabel(activeUpdate.kind)+"："+statusText(activeUpdate.status):"目标操作正在运行。"}`:!d.configured?(d.configuration_error||"目标未配置。"):state.browserDirty||state.data.dirty?"将保存点击时的草稿快照，再生成并应用。":d.automatic_deployment_enabled?"常规更新通过检查后自动部署，大改动等待批准。":"自动部署未启用，生成后可人工批准。";
    const selectedRelease=(state.data.releases||[]).find(item=>item.release_id===selected?.release_id),dataCommit=selectedRelease?.data_commit||"";$("#deploy-version-summary").textContent=selected?`已检查版本 ${selected.release_id.slice(0,12)} · 规则数据 ${dataCommit.slice(0,12)||"记录缺失"}`:"生成并应用完成后，这里会显示固定版本。";
    deployButton.disabled=!d.deployment_enabled||!selected||!attestationValid||deploymentBusy;
    manualReason.textContent=deploymentBusy?`${activeUpdate?taskLabel(activeUpdate.kind)+"："+statusText(activeUpdate.status):"目标操作正在运行。"}`:!d.deployment_enabled?"维护禁用；新部署不会启动。":!selected?"先生成并应用规则。":!attestationValid?"该版本的目标验证已过期，请重新检查此版本。":selected.application_result?.reason||"此版本等待批准；请查看下方差异和原因。";
    if(reprepareButton){reprepareButton.disabled=(!selected&&!candidate)||deploymentBusy||!d.configured;reprepareButton.textContent=selected?`重新检查所选版本 ${selected.release_id.slice(0,12)}`:candidate?`重新检查当前候选 ${candidate.slice(0,12)}`:"重新检查当前候选";}
    const successful=(d.deployments||[]).filter(p=>p.phase==="succeeded"&&p.release_id&&p.request_id).sort((a,b)=>String(b.task_updated_at||"").localeCompare(String(a.task_updated_at||"")));rollbackSelect.replaceChildren();if(!successful.length){const option=document.createElement("option");option.value="";option.textContent="暂无可回退记录";rollbackSelect.append(option);}else successful.forEach((p,index)=>{const option=document.createElement("option");option.value=p.request_id;option.textContent=`${p.release_id.slice(0,12)} · ${displayTime(p.task_updated_at)}`;option.selected=state.rollbackExplicit?p.request_id===priorRollback:index===0;rollbackSelect.append(option);});rollbackButton.disabled=!successful.length||deploymentBusy;rollbackReason.textContent=deploymentBusy?(activeUpdate?taskLabel(activeUpdate.kind)+"："+statusText(activeUpdate.status):"目标操作正在运行。"):successful.length?`将回退到${state.rollbackExplicit?"所选部署记录":"最近一次成功部署"}的旧版本。`:"没有已完成部署记录。";
    records.replaceChildren();const rows=[...(d.preparations||[]).map(p=>({...p,kind:"准备"})),...(d.deployments||[]).map(p=>({...p,kind:"部署"}))].sort((a,b)=>String(b.task_updated_at||"").localeCompare(String(a.task_updated_at||""))).slice(0,12);if(!rows.length){records.innerHTML='<p class="empty-state">尚无部署操作。</p>';}else rows.forEach(p=>{const row=document.createElement("div");row.className=`task-row ${p.phase||""}`;const title=document.createElement("strong");const phase=p.application_result?.phase||p.phase;const phaseLabel=({auto_deployed:"已自动部署",approval_required:"等待批准",unchanged:"无需更新",validated:"已检查，等待处理",succeeded:"已部署",failed:"失败",rolled_back:"已回退"})[phase]||"处理中";title.textContent=`${p.kind} · ${phaseLabel} · ${(p.release_id||"").slice(0,12)}`;const details=document.createElement("p");details.textContent=p.error||p.rollback_cause||p.application_result?.reason||p.intent?.operation||`请求 ${p.request_id}`;row.append(title,details);records.append(row);});
    const diffBox=$("#deployment-diff"),diffRelease=selected?.release_id||candidate,diffIdentity=diffRelease?`${diffRelease}|${observed?.observed_at||"unknown"}|${JSON.stringify(observed?.targets||{})}`:null;if(diffRelease&&state.diffIdentity!==diffIdentity&&state.diffPending!==diffIdentity){state.diffPending=diffIdentity;state.diff=null;request(`/api/diff?release_id=${encodeURIComponent(diffRelease)}`).then(result=>{if(state.diffPending!==diffIdentity)return;if(result.release_id!==diffRelease)throw new Error("返回的差异版本与当前选择不一致");state.diff=result;state.diffIdentity=diffIdentity;renderDeployment();}).catch(error=>{if(state.diffPending===diffIdentity)diffBox.textContent=`差异读取失败：${error.message}`;});}if(!diffRelease){diffBox.textContent="完成检查后显示与两端当前版本的差异。";}else if(state.diff?.release_id===diffRelease){const diff=state.diff;deployButton.disabled=deployButton.disabled||diff.unchanged;const observedAt=diff.baseline_observed_at?`（读回 ${displayTime(diff.baseline_observed_at)}）`:"";if(!diff.comparison_available){diffBox.textContent=`${diff.baseline_label}${observedAt}。候选规则 ${diff.rule_count} 条。${(diff.reasons||[]).join("；")}`;diffBox.dataset.detail="";}else{diffBox.replaceChildren();const summary=document.createElement("p");summary.textContent=`${diff.baseline_label}${observedAt} ${diff.baseline_release_id.slice(0,12)}：新增 ${diff.added_count} 条，删除 ${diff.removed_count} 条，策略变化 ${diff.strategy_change_count} 条。${diff.unchanged?"无需更新":({auto_allowed:"常规更新",approval_required:"等待批准",blocked:"检查未通过"}[diff.decision]||"")}：${(diff.reasons||[]).join("；")}`;diffBox.append(summary);const detail=document.createElement("details"),title=document.createElement("summary"),pre=document.createElement("pre");title.textContent="查看差异明细";pre.textContent=JSON.stringify({added_rules:diff.added_rules,removed_rules:diff.removed_rules,strategy_changes:diff.strategy_changes,category_strategy_changes:diff.category_strategy_changes},null,2);detail.append(title,pre);diffBox.append(detail);}}
  }
  function renderReleases(){
    const releases=state.data.releases||[],detail=$("#candidate-detail"),links=$("#report-links"),history=$("#release-history");links.replaceChildren();history.replaceChildren();$("#candidate-downloads").replaceChildren();
    const candidateId=state.data.candidate_release_id,current=releases.find(item=>item.release_id===candidateId);
    if(!current){detail.textContent=releases.length?"最近下载的规则不是当前候选（输入快照已过期或候选未提升）。":"还没有下载过规则版本。";detail.className="candidate-detail empty-state";$("#candidate-short").textContent=candidateId?candidateId.slice(0,12):"尚无";$("#candidate-time").textContent=candidateId?"当前候选版本":"等待首个校验版本";appendLatestBuild(detail);if(releases.length){const heading=document.createElement("p");heading.className="eyebrow";heading.textContent="已校验历史版本（非候选）";history.append(heading);releases.forEach(item=>{const row=document.createElement("div");row.className="release-row";const id=document.createElement("span");id.className="candidate-id";id.textContent=item.release_id.slice(0,12);const time=document.createElement("small");time.textContent=displayTime(item.created_at);row.append(id,time);history.append(row);});}return;}
    const manifest=current.metadata?.manifest||{};$("#candidate-short").textContent=current.release_id.slice(0,12);$("#candidate-time").textContent=`CI 完成 ${displayTime(current.metadata?.completed_at)}`;
    detail.replaceChildren();detail.className="candidate-detail";const id=document.createElement("div");id.className="candidate-id";id.textContent=current.release_id;detail.append(id);const stats=document.createElement("div");stats.className="candidate-stats";[["规则",manifest.statistics?.rule_count??"—"],["分类",Object.keys(manifest.category_status||{}).length||"—"],["来源 SHA",current.data_commit.slice(0,8)]].forEach(([label,value])=>{const box=document.createElement("div");box.className="stat";const strong=document.createElement("strong");strong.textContent=value;const small=document.createElement("span");small.textContent=label;box.append(strong,small);stats.append(box);});detail.append(stats);
    const synced=state.data.synced_commit;if((synced&&current.data_commit!==synced)||state.browserDirty||state.data.dirty){const stale=document.createElement("p");stale.className="tag warning";stale.textContent=`当前候选来自保存版本 ${current.data_commit.slice(0,12)}${synced?`；最新保存版本是 ${synced.slice(0,12)}`:""}${state.browserDirty||state.data.dirty?"；页面或服务器草稿还有未同步修改":""}。部署前请明确确认仍要使用这个旧候选。`;detail.append(stale);}
    const deployStatus=document.createElement("p");deployStatus.className=manifest.deployment_allowed?"tag success":"tag warning";deployStatus.textContent=manifest.deployment_allowed?"候选静态门禁通过":"候选静态门禁受阻";detail.append(deployStatus);
    const runtime=document.createElement("p");runtime.className="small muted";runtime.append(document.createTextNode("设备运行状态以"));const deploymentLink=document.createElement("a");deploymentLink.href="#deployment";deploymentLink.textContent="部署工作区最近一次目标读回";runtime.append(deploymentLink,document.createTextNode("为准。"));detail.append(runtime);
    const blockers=manifest.deployment_blockers||[];if(blockers.length){const review=document.createElement("details"),summary=document.createElement("summary"),list=document.createElement("ul");summary.textContent=`查看候选静态门禁原因（${blockers.length}）`;blockers.forEach(item=>{const li=document.createElement("li"),parts=[item.reason||"未分类原因"];if(item.domain)parts.push(`${item.match_type||"域名"} ${item.domain}`);if(item.categories?.length)parts.push(`类别：${item.categories.join("、")}`);if(item.entries?.length)parts.push(`审核项：${item.entries.join("、")}`);if(item.count!==undefined)parts.push(`数量：${item.count}`);if(item.category)parts.push(`类别：${item.category}`);li.textContent=parts.join(" · ");list.append(li);});review.append(summary,list);detail.append(review);}
    appendLatestBuild(detail,current);
    ["statistics.json","shared-domains.json","excluded-rules.json","conflicts.json","unsupported.json","source-inventory.json"].forEach(name=>{const a=document.createElement("a");a.className="text-link";a.href=`/api/report/${current.release_id}/${name}`;a.textContent=name.replace(".json","");links.append(a);});
    loadCandidateDownloads(current.release_id);
    const previous=releases.filter(item=>item.release_id!==current.release_id);if(previous.length){const heading=document.createElement("p");heading.className="eyebrow";heading.textContent="此前校验版本";history.append(heading);previous.forEach(item=>{const row=document.createElement("div");row.className="release-row";const id=document.createElement("span");id.className="candidate-id";id.textContent=item.release_id.slice(0,12);const time=document.createElement("small");time.textContent=displayTime(item.created_at);row.append(id,time);history.append(row);});}
  }
  function appendLatestBuild(detail,current=null){
    const build=state.data.latest_build;if(!build)return;
    const review=build.payload?.publish_review;
    const failedStatuses=["blocked","failed","interrupted"];
    if(!failedStatuses.includes(build.status)&&!review)return;
    const box=document.createElement("div");box.className="notice warning";const title=document.createElement("strong");title.textContent=review?"最新保存配置的构建已完成，但发布审核门禁阻止发布":build.status==="blocked"?"最新构建被门禁阻止":build.status==="interrupted"?"最新构建已中断":"最新构建失败";box.append(title);
    const identity=document.createElement("p");identity.textContent=`数据版本 ${build.payload?.data_commit?.slice(0,12)||"未知"} · 任务 ${build.status}`;box.append(identity);
    if(build.message){const message=document.createElement("p");message.textContent=build.message;box.append(message);}
    if(current&&build.payload?.data_commit===state.data.synced_commit&&current.data_commit!==build.payload.data_commit){const p=document.createElement("p");p.textContent="因此当前候选仍是较早版本；这次构建没有替换候选。";box.append(p);}
    if(review?.publish_blockers?.length){const list=document.createElement("ul");review.publish_blockers.forEach(item=>{const li=document.createElement("li"),parts=[item.category&&`类别 ${item.category}`,item.source_id&&`来源 ${item.source_id}`,item.reason,item.detail];if(item.rules?.length)item.rules.forEach(rule=>parts.push([rule.category,rule.match_type,rule.domain].filter(Boolean).join(" ")));li.textContent=parts.filter(Boolean).join(" · ");list.append(li);});box.append(list);}
    const recordLink=document.createElement("a");recordLink.href="#records";recordLink.className="text-link";recordLink.textContent="查看操作记录";box.append(recordLink);
    if(build.workflow_url){const link=document.createElement("a");link.href=build.workflow_url;link.target="_blank";link.rel="noopener noreferrer";link.className="text-link";link.textContent=`查看 GitHub Actions（run ${review?.run_id||build.payload?.run_id}）`;box.append(link);}
    detail.append(box);
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
  $("#save-button").addEventListener("click",async()=>{if(state.busy.save||state.busy.build||(!state.browserDirty&&!state.data.dirty))return;state.busy.save=true;renderActionControls();try{const revision=state.editRevision,snapshot=structuredClone(state.data.state);await request("/api/draft",{method:"PUT",body:JSON.stringify({state:snapshot})});state.data.dirty=true;if(revision!==state.editRevision){render();notice("已保存点击时的本机草稿；期间新增的浏览器修改仍未保存，请再次同步。",true);return;}state.browserDirty=false;render();const task=await request("/api/save",{method:"POST",body:"{}"});notice("GitHub 同步任务已排队；完成后即可运行 CI 构建。");await poll(task.task_id);}catch(error){notice(error.message,true);}finally{state.busy.save=false;renderActionControls();}});
  $("#reload-button").addEventListener("click",async()=>{if(!confirm("从 GitHub 重新载入会丢弃当前本机草稿和未保存的浏览器编辑。继续吗？"))return;try{const result=await request("/api/reload",{method:"POST",body:"{}"});state.browserDirty=false;state.editRevision++;invalidateBatchPreview();state.data=await request("/api/state");render();notice(`已载入 GitHub 规则 ${result.synced_commit.slice(0,8)}。`);}catch(error){notice(error.message,true);}});
  $("#build-button").addEventListener("click",async()=>{if(state.busy.build||state.busy.save||state.browserDirty||state.data.dirty)return;state.busy.build=true;renderActionControls();try{const task=await request("/api/build",{method:"POST",body:"{}"});notice("GitHub Actions 构建已排队；完成后会校验并更新候选。");await poll(task.task_id);}catch(error){notice(error.message,true);}finally{state.busy.build=false;renderActionControls();}});
  async function runDeploymentAction(action,successMessage){state.busy.deployment=true;renderDeployment();try{const result=await action();if(!result)return;notice(successMessage);await poll(result.task_id);}catch(error){notice(error.message,true);}finally{state.busy.deployment=false;renderDeployment();}}
  $("#generate-prepare").addEventListener("click",()=>runDeploymentAction(async()=>{const revision=state.editRevision,snapshot=structuredClone(state.data.state);if(state.browserDirty){await request("/api/draft",{method:"PUT",body:JSON.stringify({state:snapshot})});state.data.dirty=true;state.browserDirty=revision!==state.editRevision;renderActionControls();}return request("/api/deployment/generate",{method:"POST",body:"{}"});},"生成与应用任务已排队；常规更新可自动部署，大改动等待批准。"));
  $("#reprepare-deployment").addEventListener("click",()=>runDeploymentAction(async()=>{const prep=(state.data.deployment?.preparations||[]).find(item=>item.request_id===$("#prepared-request").value);const releaseId=prep?.release_id||state.data.candidate_release_id;if(!releaseId)throw new Error("请选择需要重新检查的版本");return request("/api/deployment/reprepare",{method:"POST",body:JSON.stringify({release_id:releaseId})});},"该固定版本的双目标准备已排队；不会激活服务。"));
  $("#manual-deployment").addEventListener("click",()=>runDeploymentAction(async()=>{const requestId=$("#prepared-request").value;if(!requestId)throw new Error("请先生成并应用规则");const prep=(state.data.deployment?.preparations||[]).find(item=>item.request_id===requestId);if(!prep)throw new Error("准备记录已不存在，请刷新并重新选择");const diff=await request(`/api/diff?release_id=${encodeURIComponent(prep.release_id)}`);if(diff.release_id!==prep.release_id)throw new Error("差异版本与所选准备记录不一致，请刷新后重试");if(diff.unchanged)throw new Error("两端已运行此版本，无需部署");state.diff=diff;state.diffIdentity=null;const release=(state.data.releases||[]).find(item=>item.release_id===prep.release_id),dataCommit=release?.data_commit||"版本记录缺失",old=state.data.synced_commit&&dataCommit!==state.data.synced_commit;const changes=diff.comparison_available?`与两端版本 ${diff.baseline_release_id.slice(0,12)} 对比：新增 ${diff.added_count}，删除 ${diff.removed_count}，策略变化 ${diff.strategy_change_count}。`:`${diff.baseline_label||"没有可验证的实际版本基线"}。需要先人工检查并重新准备此固定版本。`;const attention=diff.decision==="blocked"?`人工检查提示：${(diff.reasons||[]).join("；")}`:"";const dirty=state.browserDirty||state.data.dirty;const prompt=`准备激活版本 ${prep.release_id.slice(0,12)}（规则数据 ${dataCommit.slice(0,12)}）。${changes}${attention}${old?`此版本早于最新保存版本 ${state.data.synced_commit.slice(0,12)}。`:""}${dirty?"页面或服务器草稿有未同步修改；仍要部署这个旧候选吗？":""}${old||dirty?"如果确认使用，请明确选择继续。":""}继续部署？`;if(!confirm(prompt))return null;return request("/api/deployment/manual",{method:"POST",body:JSON.stringify({request_id:requestId})});},"部署任务已排队；系统会读回两端状态，失败时尝试回退。"));
  $("#rollback-deployment").addEventListener("click",()=>runDeploymentAction(async()=>{const requestId=$("#rollback-request").value;if(!requestId)throw new Error("暂无可回退部署");if(!confirm("将两个目标回退到这次部署记录的旧版本，并验证读回。继续？"))return null;return request("/api/deployment/rollback",{method:"POST",body:JSON.stringify({request_id:requestId})});},"人工回退任务已排队。"));
  $("#prepared-request").addEventListener("change",()=>{state.preferredPreparationRequest=null;renderDeployment();});$("#rollback-request").addEventListener("change",()=>{state.rollbackExplicit=true;renderDeployment();});
  async function poll(taskIds=null){
    if(state.timer)clearInterval(state.timer);
    const tracked=new Set(Array.isArray(taskIds)?taskIds:taskIds?[taskIds]:[]),activeStatuses=["queued","running","polling","downloading"],terminalStatuses=["succeeded","failed","blocked","interrupted"];
    const stop=()=>{if(state.timer)clearInterval(state.timer);state.timer=null;};
    const once=async()=>{
      try{
        const result=await request("/api/tasks");state.data.tasks=result.tasks;
        const active=result.tasks.filter(task=>activeStatuses.includes(task.status));active.forEach(task=>tracked.add(task.id));
        renderTasks();renderActionControls();renderDeployment();
        const completed=result.tasks.filter(task=>tracked.has(task.id)&&terminalStatuses.includes(task.status));
        if(completed.length){completed.filter(task=>task.status==="succeeded"&&(task.kind==="generate_prepare"||task.kind==="prepare_deploy")).forEach(task=>{const requestId=task.payload?.prepare_request_id||task.payload?.request_id;if(requestId)state.preferredPreparationRequest=requestId;});completed.forEach(task=>tracked.delete(task.id));const reload=await request("/api/state");mergeServerData(reload);state.data.tasks=result.tasks;render();const task=completed[0];notice(task.status==="succeeded"?`${taskLabel(task.kind)}：${taskStatus(task)}。`:taskMessage(task),task.status!=="succeeded");}
        if(!active.length)stop();
      }catch(error){notice(error.message,true);}
    };
    await once();
    if(state.data.tasks?.some(task=>activeStatuses.includes(task.status)))state.timer=setInterval(once,3000);
  }
  $("#refresh").addEventListener("click",async()=>{try{mergeServerData(await request("/api/state"));render();notice(state.browserDirty?"已刷新记录；保留未保存的浏览器草稿。":"已刷新本地记录。");}catch(error){notice(error.message,true);}});
  $("#diff-button").addEventListener("click",async()=>{try{$("#inspect-result").hidden=false;$("#inspect-result").textContent=JSON.stringify(await request("/api/diff"),null,2);}catch(error){notice(error.message,true);}});
  $("#rule-search").addEventListener("input",renderRules);$("#rule-filter-category").addEventListener("change",renderRules);$("#rule-filter-status").addEventListener("change",renderRules);$("#mapping-search").addEventListener("input",renderCategories);
  $("#catalog-search").addEventListener("input",()=>{invalidateBatchPreview();renderCatalog();});$("#catalog-group-filter").addEventListener("change",()=>{invalidateBatchPreview();renderCatalog();});$("#catalog-status-filter").addEventListener("change",()=>{invalidateBatchPreview();renderCatalog();});
  $("#batch-enable-preview").addEventListener("click",()=>previewCategoryBatch("enable").catch(error=>notice(error.message,true)));
  $("#batch-disable-preview").addEventListener("click",()=>previewCategoryBatch("disable").catch(error=>notice(error.message,true)));
  $("#batch-apply").addEventListener("click",async()=>{if(!state.batchAction||state.batchPreviewRevision!==state.editRevision)return;const action=state.batchAction,ids=[...state.batchPreviewIds],revision=state.editRevision,requestSequence=state.previewSequence;try{const result=await request("/api/categories/batch-apply",{method:"POST",body:JSON.stringify({action,service_ids:ids})});if(revision!==state.editRevision){state.data.dirty=true;invalidateBatchPreview();notice("批量修改已应用到此前保存的草稿快照；当前较新的浏览器编辑已保留，请重新预览批量修改。",true);return;}if(requestSequence!==state.previewSequence){mergeServerData({...state.data,state:result.state,dirty:true});invalidateBatchPreview();render();notice("批量修改已应用到本机草稿；请重新确认当前筛选和选择。",true);return;}state.data.state=result.state;state.data.dirty=true;state.browserDirty=false;state.catalogSelected.clear();state.batchAction=null;state.batchPreviewRevision=null;state.batchPreviewIds=null;$("#batch-apply").disabled=true;$("#batch-result").hidden=true;render();notice("批量分类修改已加入本机草稿；请保存到 GitHub。");}catch(error){notice(error.message,true);}});
  $("#explain-button").addEventListener("click",async()=>{const domain=prompt("输入要解释的域名");if(!domain)return;try{const result=await request(`/api/explain?domain=${encodeURIComponent(domain)}`),box=$("#inspect-result");box.replaceChildren();box.hidden=false;const pre=document.createElement("pre");pre.textContent=JSON.stringify(result,null,2);box.append(pre);for(const row of result.matches||[]){if(row.custom)continue;const button=document.createElement("button");button.className="secondary compact";button.textContent=`排除此条来源规则 · ${row.category} · ${row.match_type} ${row.domain}`;button.addEventListener("click",async()=>{const reviewer=prompt("审核人（公开记录）");if(!reviewer)return;const reason=prompt("排除理由（公开记录）");if(!reason)return;try{await request("/api/exclusions",{method:"POST",body:JSON.stringify({category:row.category,domain:row.domain,match_type:row.match_type,reviewer,reason})});state.data=await request("/api/state");state.data.dirty=true;render();notice("精确来源排除已加入本机草稿；保存到 GitHub 后才会进入构建。");}catch(error){notice(error.message,true);}});box.append(button);}}catch(error){notice(error.message,true);}});
  $("#download-history").addEventListener("click",async()=>{try{const result=await request("/api/audit");const blob=new Blob([JSON.stringify(result,null,2)],{type:"application/json"}),url=URL.createObjectURL(blob),link=document.createElement("a");link.href=url;link.download="dns-rule-audit.json";link.click();URL.revokeObjectURL(url);}catch(error){notice(error.message,true);}});
  $("#strategy-refresh").addEventListener("click",()=>{if(strategyDraftChanged()&&!confirm("刷新会丢弃当前尚未应用的成员草稿和应用后选择。继续刷新目标实际状态吗？"))return;loadStrategies();});
  $("#strategy-group-search").addEventListener("input",renderStrategyGroups);
  $("#strategy-catalog-search").addEventListener("input",()=>renderStrategyCatalog(currentStrategyGroup(),strategyAvailable()));
  $("#strategy-select-save").addEventListener("click",async()=>{
    const group=currentStrategyGroup();if(!group||!strategyAvailable().enabled)return;
    const members=[...(group.members||[])],selected=members.find(ref=>strategyRefKey(ref)===state.strategy.selectedChoice);
    if(!selected){notice("只能选择这个策略组的现有可用成员。",true);return;}
    await submitStrategyOperation("/api/strategies/select",{group:group.name,revision:state.strategy.data.revision,members,selected},"策略选择保存");
  });
  $("#strategy-preview").addEventListener("click",async()=>{
    const group=currentStrategyGroup();if(!group||!strategyInspectAvailable().enabled)return;
    const refs=[...(group.members||[]),...(state.strategy.data.catalog||[]).map(item=>item.ref).filter(Boolean)],members=[...state.strategy.editorMembers].map(key=>refs.find(ref=>strategyRefKey(ref)===key)).filter(Boolean),selected=refs.find(ref=>strategyRefKey(ref)===state.strategy.draftSelectedChoice);
    if(!selected){notice("请选择一个仍保留在成员草稿中的选项作为应用后的选择。",true);return;}
    const payload={group:group.name,revision:state.strategy.data.revision,members,selected},serial=++state.strategy.previewSerial;state.strategy.preview=null;state.strategy.previewing=true;renderStrategyEditState();
    try{
      const result=await request("/api/strategies/preview",{method:"POST",body:JSON.stringify(payload)});
      const sameMembers=Array.isArray(result.members)&&result.members.length===members.length&&result.members.every(ref=>members.some(value=>strategyRefKey(value)===strategyRefKey(ref)));
      const sameSelection=strategyRefKey(result.selected)===strategyRefKey(selected),stillCurrent=serial===state.strategy.previewSerial&&state.strategy.groupName===group.name&&state.strategy.data?.revision===payload.revision&&state.strategy.editorMembers.size===members.length&&members.every(ref=>state.strategy.editorMembers.has(strategyRefKey(ref))),fresh=stillCurrent&&result.group===group.name&&result.revision===payload.revision&&sameMembers&&sameSelection&&/^([0-9a-f]{64})$/.test(result.preview_sha256||"");
      state.strategy.preview={...result,ok:Boolean(result.ok&&fresh),local_error:fresh?null:"预览与当前组、成员、选择或配置版本不匹配，已作废。",payload};
      if(serial===state.strategy.previewSerial)renderStrategyEditState();
    }catch(error){if(serial===state.strategy.previewSerial){state.strategy.preview={ok:false,error:error.message};renderStrategyEditState();notice(error.message,true);}}
    finally{if(serial===state.strategy.previewSerial){state.strategy.previewing=false;renderStrategyEditState();}}
  });
  $("#strategy-apply").addEventListener("click",async()=>{
    const group=currentStrategyGroup(),preview=state.strategy.preview;if(!group||!preview?.ok||!strategyAvailable().enabled)return;
    state.strategy.pendingApply={...preview.payload,preview_sha256:preview.preview_sha256};
    $("#strategy-confirm-group").textContent=group.name;
    $("#strategy-confirm-member-count").textContent=`${preview.payload.members.length} 个`;
    $("#strategy-confirm-selection").textContent=strategyRefLabel(preview.payload.selected);
    $("#strategy-apply-dialog").showModal();
    $("#strategy-apply-cancel").focus();
  });
  $("#strategy-apply-cancel").addEventListener("click",()=>{$("#strategy-apply-dialog").close();state.strategy.pendingApply=null;});
  $("#strategy-apply-dialog").addEventListener("cancel",()=>{state.strategy.pendingApply=null;});
  $("#strategy-apply-dialog").addEventListener("close",()=>{state.strategy.pendingApply=null;});
  $("#strategy-apply-confirm").addEventListener("click",async()=>{
    const payload=state.strategy.pendingApply;state.strategy.pendingApply=null;$("#strategy-apply-dialog").close();
    if(!payload)return;
    const available=strategyAvailable();if(!available.enabled){notice(available.reason,true);return;}
    await submitStrategyOperation("/api/strategies/apply",payload,"策略组成员应用");
  });
  async function loadCandidateDownloads(releaseId){try{const data=await request(`/api/files/${releaseId}`),box=$("#candidate-downloads");box.replaceChildren();const bundle=document.createElement("a");bundle.className="text-link";bundle.href=data.bundle;bundle.textContent="完整规则包 ZIP";box.append(bundle);data.files.forEach(path=>{const a=document.createElement("a");a.className="text-link";a.href=`/api/file/${releaseId}/${path.split("/").map(encodeURIComponent).join("/")}`;a.download=path.split("/").at(-1);a.textContent=path;box.append(a);});}catch(error){notice(error.message,true);}}
  $("#logout").addEventListener("click",async()=>{try{await request("/logout",{method:"POST",body:""});location.assign("/login");}catch(error){notice(error.message,true);}});
  window.addEventListener("hashchange",()=>syncWorkspaceFromHash(true));
  async function load(){try{state.data=await request("/api/state");state.categories=Object.fromEntries(Object.entries(state.data.categories).map(([id,cfg])=>[id,{...cfg,label:cfg.provider_name}]));state.policies=state.data.policies;const selector=$("#rule-category"),filter=$("#rule-filter-category");Object.entries(state.categories).forEach(([id,cfg])=>{const option=document.createElement("option");option.value=id;option.textContent=`${cfg.label} · ${id}`;selector.append(option);const filterOption=option.cloneNode(true);filter.append(filterOption);});render();syncWorkspaceFromHash(true);const active=(state.data.tasks||[]).filter(task=>["queued","running","polling","downloading"].includes(task.status));if(active.length)poll(active.map(task=>task.id));}catch(error){notice(error.message,true);}}
  load();
})();
