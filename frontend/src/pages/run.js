import {feedback, field} from '../components/common/fields.js';
import {empty, panel, pill} from '../components/common/panels.js';
import {table} from '../components/common/table.js';
import {RUN_KIND, STATUS} from '../constants/enums.js';
import {handler} from '../hooks/action.js';
import {session} from '../stores/session.js';
import {workspace} from '../stores/workspace.js';
import {button, el, link, p} from '../utils/dom.js';
import {date} from '../utils/format.js';

function runRequest(data, spec) {
  const kind = data.get('kind') || 'analysis';
  const body = {dataset_id:Number(data.get('dataset_id')),name:data.get('name'),kind,
    params:Object.fromEntries(spec.params.map(param=>[param.key,data.get(param.key)]))};
  if (kind === 'reconcile') body.aux_dataset_id = Number(data.get('aux_dataset_id'));
  return body;
}

function picker(name, label, datasets, selected) {
  return el('label',{for:`field-${name}`,class:'field'},el('span',{},label),
    el('select',{id:`field-${name}`,name,required:true},datasets.map(item=>
      el('option',{value:item.id,selected:String(item.id) === String(selected)},`${item.name} · ${item.row_count} 行`))));
}

export async function runPage(route, refresh) {
  const {spec,datasets,runs} = workspace;
  const scheduling = spec.scheduled && route.scheduling;
  const plans = scheduling ? await workspace.schedules() : [];
  const error = feedback();
  const submit = button(`执行${spec.run}`,{type:'submit',class:'primary','data-testid':'start-run'});
  const plan = button('保存定时计划',{type:'button',class:'primary','data-testid':'save-schedule'});
  const scheduleFields = scheduling && session.canReview && el('details',{class:'schedule-fields'},
    el('summary',{},'定时站内分发'),
    p('单次计划使用选定数据批次。每日计划沿用同一批次，业务按报告日推进时会使用最新批次。计算通过发布条件后进入站内收件箱。','subtle'),
    field({key:'recipient',label:'收件团队',type:'select',required:false,options:spec.recipients}),
    field({key:'first_at',label:'首次执行时间（北京时间）',type:'datetime-local',required:false}),
    el('label',{class:'check'},el('input',{name:'repeat_daily',type:'checkbox'}),'每日同一时间运行'), plan);
  const ofKind = key => datasets.filter(item => (item.kind || 'ledger') === key);
  const host = el('div',{class:'form-grid'});
  const mode = el('select',{id:'run-kind',name:'kind'},
    (spec.run_modes || [{key:'analysis',label:'台账关联分析'}]).map(item=>el('option',{value:item.key},item.label)));
  const paint = () => {
    if (mode.value === 'reconcile') {
      const departures = ofKind('departures'), checks = ofKind('checks');
      host.replaceChildren(
        picker('dataset_id','发车记录批次（调度交接系统）',departures,route.id),
        picker('aux_dataset_id','候车清点批次（站点清点系统）',checks,route.id),
        !(departures.length && checks.length) &&
          p('跨系统对账需要发车记录和候车清点各一个批次，请先到数据页导入对应类型。','feedback'));
    } else {
      host.replaceChildren(picker('dataset_id','数据批次',ofKind('ledger'),route.id));
    }
  };
  mode.addEventListener('change',paint);
  const preselected = datasets.find(item => item.id === route.id);
  if (preselected && preselected.kind && preselected.kind !== 'ledger') mode.value = 'reconcile';
  paint();
  const guide = spec.reconcile_guide && p(spec.reconcile_guide,'subtle');
  const syncGuide = () => { if (guide) guide.hidden = mode.value !== 'reconcile'; };
  mode.addEventListener('change',syncGuide);
  syncGuide();
  const form = el('form',{class:'fields','data-testid':'run-form',
    onSubmit:handler(submit,error,async event=>{
      const item = await workspace.start(runRequest(new FormData(event.target),spec));
      location.hash = `#results/${item.id}`;
    })}, field({key:'name',label:'运行名称',type:'string',max_length:120},`样本${spec.run}`),
    el('label',{class:'field',for:'run-kind'},el('span',{},'运行类型'),mode), host, guide,
    el('div',{class:'form-grid'},spec.params.map(param=>field(param))),el('div',{class:'actions'},submit),scheduleFields,error);
  plan.addEventListener('click',handler(plan,error,async()=>{
    if (!form.reportValidity()) return;
    const data = new FormData(form);
    if (data.get('kind') === 'reconcile') throw new Error('定时站内分发暂支持台账关联分析，跨系统对账请手动执行');
    const recipient = data.get('recipient'), start = data.get('first_at');
    if (!recipient?.trim() || !start) throw new Error('请填写收件团队和首次执行时间');
    await workspace.schedule({...runRequest(data,spec),recipient,first_at:`${start}:00+08:00`,repeat_daily:data.has('repeat_daily')});
    await refresh();
  }));
  const runList = table([
    {label:'运行',render:item=>link(item.name,`#results/${item.id}`)},
    {label:'类型',render:item=>RUN_KIND[item.kind || 'analysis']},
    {label:'状态',render:item=>pill(STATUS[item.status],item.status)},
    {label:'时间',render:item=>date(item.created_at)},
  ],runs,{caption:'运行记录',empty:'首次运行后将在此显示后台状态。'});
  const scheduleList = scheduling && panel('定时计划',table([
    {key:'name',label:'计划'}, {key:'recipient',label:'收件团队'},
    {label:'下次执行（北京时间）',render:row=>row.enabled ? date(row.next_at) : '—'},
    {key:'next_report_day',label:'下次报告日'},
    {label:'状态',render:row=>row.enabled ? '启用' : '已结束'},
    {label:'操作',render:row=>{
      if (!row.enabled || !session.canReview) return '—';
      const action = button('停止计划',{class:'quiet'});
      action.addEventListener('click',handler(action,error,async()=>{await workspace.disable(row.id);await refresh();}));
      return action;
    }},
  ],plans,{caption:'定时计划',empty:'还未配置定时计划。'}));
  return el('div',{class:'two-col'}, el('div',{class:'wide-panel'},
    panel(spec.run,session.canWrite ? (datasets.length ? form : empty('请先导入数据。','#inputs','前往导入'))
      : p('只读角色可以查看上方业务流程的历史运行。','empty')),
    !scheduling && panel('运行记录',runList),scheduleList),
    el('aside',{class:'sidebar-panel'},panel('计算口径',el('ul',{class:'method-list'},spec.methodology.map(text=>el('li',{},text))))));
}
