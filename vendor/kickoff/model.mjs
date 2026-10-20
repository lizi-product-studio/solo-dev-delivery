export const VERSION=1;
const o=(value,label,description='')=>({value,label,description});
export const has=(a,v)=>Array.isArray(a)&&a.includes(v);
export const isAI=d=>has(d.types,'agent')||has(d.features,'ai');
export const isPayment=d=>has(d.features,'payment')||(has(d.types,'mini')&&has(d.miniServices,'pay'));
export const hasExternal=d=>isAI(d)||isPayment(d)||has(d.features,'sync')||has(d.features,'notifications')||has(d.types,'mini')||has(d.types,'automation');
export const fields=[
 {id:'types',step:0,label:'产品类型',required:true,kind:'multi',cards:true,options:[o('admin','后台系统'),o('agent','Agent / AI 助手'),o('web','网站 / Web 应用'),o('mini','微信小程序'),o('desktop','桌面软件'),o('mobile','手机 App'),o('automation','自动化工具'),o('other','其他 / 待推荐')]},
 {id:'depth',step:0,label:'交付目标',required:true,kind:'single',cards:true,options:[o('prototype','交互原型','页面与操作，数据可模拟'),o('demo','系统 Demo','前后台、接口和数据联动'),o('launch','上线版本','真实集成、验证与部署'),o('advice','待推荐','先评估范围与取舍')]},
 {id:'goal',step:0,label:'业务目标',required:true,kind:'textarea',placeholder:'给谁用，解决什么问题',max:2000},
 {id:'name',step:0,label:'项目名称',kind:'text',placeholder:'项目名称',max:100},
 {id:'stage',step:1,label:'项目阶段',kind:'single',options:[o('new','从零开始'),o('existing','已有项目迭代'),o('replace','替换旧系统'),o('unsure','待定')]},
 {id:'audience',step:1,label:'使用人群',kind:'multi',options:[o('self','个人'),o('team','内部团队'),o('customers','消费者 / 客户'),o('companies','企业 / 商户'),o('public','公众访客'),o('unsure','待定')]},
 {id:'roles',step:1,label:'用户角色',kind:'multi',options:[o('owner','超级管理员'),o('ops','运营'),o('staff','员工'),o('customer','普通用户'),o('merchant','商家 / 门店负责人'),o('reviewer','审核人员'),o('finance','财务'),o('suggest','待推荐')]},
 {id:'roleRules',step:1,label:'角色分工',kind:'textarea',placeholder:'如：员工只处理本人任务；店长可分配本店任务'},
 {id:'features',step:1,label:'首版功能',kind:'multi',options:[o('accounts','账号与权限'),o('crud','数据管理'),o('orders','订单 / 交易'),o('payment','支付 / 退款'),o('booking','预约 / 排班'),o('approval','审批'),o('search','搜索 / 筛选'),o('files','文件 / 图片'),o('notifications','消息提醒'),o('reports','报表 / 导出'),o('ai','AI 能力'),o('sync','第三方联动'),o('other','其他')]},
 {id:'flow',step:1,label:'核心流程',kind:'textarea',placeholder:'如：客户预约 → 店长分配 → 员工服务 → 客户确认'},
 {id:'pages',step:1,label:'页面清单',kind:'textarea',placeholder:'有哪些页面、各自做什么，如：预约列表（按日期筛选）；预约详情（分配员工）'},
 {id:'entities',step:1,label:'数据对象',kind:'textarea',placeholder:'要记录哪些数据及关键信息，如：客户（姓名、手机号）；预约（时间、服务项目、状态）'},
 {id:'rules',step:1,label:'业务规则',kind:'textarea',placeholder:'如：取消预约须提前 2 小时'},
 {id:'excluded',step:1,label:'不做 / 延后',kind:'textarea',placeholder:'本次不需要的功能'},
 {id:'scope',step:2,label:'默认数据可见范围',kind:'single',when:d=>has(d.features,'accounts')||has(d.types,'admin')||has(d.audience,'companies'),options:[o('own','仅本人'),o('org','所属团队 / 门店'),o('tenant','企业间严格隔离'),o('shared','团队共享'),o('unsure','待确认')]},
 {id:'adminWork',step:2,label:'后台主要工作',kind:'multi',when:d=>has(d.types,'admin'),options:[o('records','录入查询'),o('workflow','分配审批'),o('config','配置发布'),o('analysis','经营分析'),o('suggest','待推荐')]},
 {id:'agentJob',step:2,label:'Agent 任务',kind:'multi',when:isAI,options:[o('qa','知识问答'),o('generate','内容生成'),o('analysis','资料分析'),o('tools','操作工具'),o('workflow','多步任务'),o('suggest','待推荐')]},
 {id:'knowledge',step:2,label:'信息来源',kind:'multi',when:isAI,options:[o('input','当次输入'),o('docs','文件 / 知识库'),o('web','公开网络'),o('private','业务系统 / 私有数据'),o('unsure','待定')]},
 {id:'autonomy',step:2,label:'执行权限',kind:'single',when:d=>isAI(d)||has(d.types,'automation'),options:[o('suggest','只提供建议'),o('confirm','人工确认后执行'),o('bounded','约定范围内自动执行'),o('unsure','待确认')]},
 {id:'quality',step:2,label:'Agent 验收重点',kind:'multi',when:isAI,options:[o('accuracy','准确性与依据'),o('completion','任务完成率'),o('latency','响应速度'),o('cost','调用成本'),o('privacy','数据隐私'),o('unsure','待推荐')]},
 {id:'trigger',step:2,label:'触发方式',kind:'multi',when:d=>has(d.types,'automation'),options:[o('manual','手动'),o('schedule','定时'),o('event','消息 / 事件'),o('batch','批量文件'),o('unsure','待定')]},
 {id:'platform',step:2,label:'目标系统',kind:'multi',when:d=>has(d.types,'desktop')||has(d.types,'mobile'),options:[o('mac','macOS'),o('win','Windows'),o('linux','Linux'),o('ios','iOS'),o('android','Android'),o('unsure','待推荐')]},
 {id:'offline',step:2,label:'离线使用',kind:'single',when:d=>has(d.types,'desktop')||has(d.types,'mobile'),options:[o('yes','核心功能可离线'),o('no','仅联网使用'),o('unsure','待定')]},
 {id:'distribution',step:2,label:'安装方式',kind:'single',when:d=>has(d.types,'desktop')||has(d.types,'mobile'),options:[o('local','仅本机'),o('internal','内部安装包'),o('store','应用商店'),o('unsure','待推荐')]},
 {id:'miniServices',step:2,label:'微信能力',kind:'multi',when:d=>has(d.types,'mini'),options:[o('login','微信登录'),o('phone','手机号授权'),o('pay','微信支付'),o('notice','订阅消息'),o('location','位置 / 地图'),o('share','分享'),o('unsure','待推荐')]},
 {id:'webFocus',step:2,label:'网站用途',kind:'single',when:d=>has(d.types,'web'),options:[o('brand','品牌展示 / 获客'),o('content','内容阅读'),o('app','在线业务'),o('portal','内部门户'),o('unsure','待定')]},
 {id:'paymentRules',step:2,label:'交易场景',kind:'multi',when:isPayment,options:[o('single','单笔付款'),o('refund','取消 / 退款'),o('partial','部分退款'),o('group','合并付款 / 拆单'),o('subscription','周期付费'),o('unsure','待确认')]},
 {id:'integration',step:2,label:'外部服务接入',kind:'single',when:hasExternal,options:[o('mock','先模拟'),o('real','必须真实接入'),o('mixed','部分真实接入'),o('unsure','待推荐')]},
 {id:'accessReady',step:2,label:'平台账号 / 接口',kind:'single',when:hasExternal,options:[o('ready','已就绪'),o('partial','部分就绪'),o('none','未准备'),o('unsure','待确认')]},
 {id:'example',step:2,label:'典型输入与预期结果',kind:'textarea',placeholder:'如：输入一份订单表 → 按门店汇总金额；附一条成功和失败的例子'},
 {id:'acceptance',step:2,label:'验收场景',kind:'textarea',placeholder:'如：切换三个角色走完一次预约流程'},
 {id:'customization',step:3,label:'开发方式',kind:'single',options:[o('reuse','优先现成方案'),o('partial','局部定制'),o('custom','重点定制（备注）')]},
 {id:'priority',step:3,label:'首要目标',kind:'single',options:[o('speed','交付速度'),o('visual','视觉与体验'),o('business','业务闭环'),o('reliability','可靠性'),o('cost','控制成本'),o('suggest','待推荐')]},
 {id:'volume',step:3,label:'初期用户规模',kind:'single',options:[o('personal','个人 / 几人'),o('small','百人以内'),o('medium','百人至万人'),o('large','万人以上'),o('unknown','待估')]},
 {id:'growth',step:3,label:'每日新增数据',kind:'single',options:[o('low','几十条以内'),o('moderate','几十至几千条'),o('high','数万条以上'),o('unknown','待估')]},
 {id:'data',step:3,label:'数据类型',kind:'multi',options:[o('demo','演示数据'),o('public','公开信息'),o('business','业务数据'),o('personal','个人信息'),o('sensitive','财务 / 机密'),o('legacy','旧数据导入')]},
 {id:'hosting',step:3,label:'运行环境',kind:'single',options:[o('local','本机'),o('private','内网 / 私有环境'),o('cloud','云端'),o('existing','已有服务器'),o('suggest','待推荐')]},
 {id:'budget',step:3,label:'每月运行预算',kind:'single',options:[o('free','尽量零成本'),o('low','500 元以内'),o('mid','500–2,000 元'),o('high','2,000 元以上'),o('estimate','待估')]},
 {id:'deadline',step:3,label:'交付节奏',kind:'single',options:[o('fast','尽快看第一版'),o('week','一周左右看 Demo'),o('quality','质量优先'),o('date','指定日期（备注）')]},
 {id:'style',step:3,label:'视觉风格',kind:'single',options:[o('clean','简洁专业'),o('bright','明亮亲和'),o('tech','科技感'),o('existing','沿用现有设计'),o('suggest','待推荐')]},
 {id:'references',step:3,label:'参考产品',kind:'textarea',placeholder:'参考链接与喜欢的部分'},
 {id:'resources',step:3,label:'已有资料',kind:'textarea',placeholder:'代码目录或仓库、接口文档、样例数据的位置；不要填写密码或密钥'},
 {id:'constraints',step:3,label:'已有条件 / 技术限制',kind:'textarea',placeholder:'已有代码、必须复用的接口、部署限制等'},
 {id:'collaboration',step:3,label:'参与方式',kind:'single',options:[o('milestones','关键节点看结果'),o('visual','先看样板 / 原型'),o('delegate','只做关键决定'),o('together','一起讨论方案')]},
 {id:'notes',step:3,label:'补充说明',kind:'textarea',placeholder:'其他功能、具体日期或特殊要求'}
];
// answers：简报页「需要你决定」各问题的答复，键为问题 id。
export const blank=()=>({...Object.fromEntries(fields.map(f=>[f.id,f.kind==='multi'?[]:''])),answers:{}});
export const visible=(f,d)=>!f.when||f.when(d);
export const answered=v=>Array.isArray(v)?v.length>0:typeof v==='string'&&v.trim().length>0;
export function activeData(d){return Object.fromEntries(fields.filter(f=>visible(f,d)).map(f=>[f.id,d[f.id]??(f.kind==='multi'?[]:'')]))}
export function label(id,v){const f=fields.find(f=>f.id===id);if(!f)return '';return Array.isArray(v)?v.map(x=>label(id,x)).join('、'):f.options?.find(x=>x.value===v)?.label||String(v||'')}
// 交付目标越接近上线，开发前需要人定下的内容越多。
export const REQUIRED_BY_DEPTH={prototype:['flow','pages'],demo:['flow','entities','acceptance'],launch:['flow','entities','rules','example','acceptance']};
export const isRequired=(f,d)=>f.required===true||has(REQUIRED_BY_DEPTH[d?.depth],f.id);
export const validate=d=>fields.filter(f=>visible(f,d)&&isRequired(f,d)&&!answered(d[f.id])).map(f=>({id:f.id,step:f.step,message:`请填写${f.label}`}));
// 选项会随版本调整：已停用的选项只移除该项并记入 issues，不让整份草稿失效。
export function normalize(input,issues=[]){if(!input||typeof input!=='object'||Array.isArray(input))throw Error('项目格式不正确');const d=blank();
 for(const f of fields){let v=input[f.id];if(v===undefined||v===null)continue;
  if(f.kind==='multi'){if(!Array.isArray(v)){issues.push(`${f.label}格式无效，已清空`);continue}const kept=v.filter(x=>typeof x==='string'&&f.options.some(o=>o.value===x));if(kept.length<v.length)issues.push(`${f.label}中已停用的选项已移除`);d[f.id]=[...new Set(kept)]}
  else{const max=f.max||5000;if(typeof v!=='string'){issues.push(`${f.label}格式无效，已清空`);continue}v=v.toWellFormed?.()??v;if(f.options&&v&&!f.options.some(o=>o.value===v)){issues.push(`${f.label}的选项已停用，已清空`);continue}if(v.length>max)issues.push(`${f.label}超出 ${max} 字，已截断`);d[f.id]=v.slice(0,max)}}
 const a=input.answers;if(a&&typeof a==='object'&&!Array.isArray(a))for(const [k,v] of Object.entries(a).slice(0,200))if(k.length<=100&&typeof v==='string')d.answers[k]=(v.toWellFormed?.()??v).slice(0,2000);
 return d}
export function recommend(raw){const d={...blank(),...activeData(raw)},ai=isAI(d),pay=isPayment(d),auto=has(d.types,'automation'),legacy=['existing','replace'].includes(d.stage)||has(d.data,'legacy');
 let mode='范围与方案确认';
 if(d.depth==='prototype')mode='原型共创';
 if(d.depth==='demo')mode='业务闭环优先';
 if(d.depth==='launch')mode='上线交付';
 const built=d.depth==='demo'||d.depth==='launch';
 if(ai&&built)mode=d.depth==='launch'?'Agent 评测与上线':'Agent 效果验证优先';
 else if(auto&&built)mode='自动化流程验证';
 else if(has(d.types,'web')&&['brand','content'].includes(d.webFocus)&&!pay)mode='内容与体验共创';
 const phases=phasesFor(d,ai,auto,legacy),first=`${phases[0].title}：${phases[0].do}`;
 const checks=[],architecture=[d.customization==='custom'?'按明确的定制范围评估实现，通用能力仍优先复用。':'默认效率优先：先评估成熟项目、模板和组件，用关键流程验证后做最小改造；不适配的部分再定制。'];
 // questions：需要人决定的问题；delegated：明确交给 AI 的事项；covers 标出对应字段，避免和字段本身的「待定 / 待推荐」重复列出。
 const questions=[],delegated=[],prepare=[],ask=(id,q,covers)=>questions.push({id,q,covers}),delegate=(text,covers)=>delegated.push({text,covers});
 if(ai){checks.push(d.depth==='prototype'?'原型里 AI 的回答可以用预设示例；如果接入真实模型，用代表性样本检查回答质量。':'用代表性样本评估准确性、依据和任务完成情况，保留失败样本。');if(d.depth==='prototype')ask('prototype-ai','原型里 AI 的回答用真实模型，还是预设的示例回答？');architecture.push('划分模型、知识源、工具和评测边界，再判断是否需要检索、记忆或多 Agent。');if(!d.autonomy||d.autonomy==='unsure')ask('agent-autonomy','Agent 可执行哪些动作，哪些需要人工确认？','autonomy');if(has(d.knowledge,'private')||has(d.data,'personal')||has(d.data,'sensitive'))ask('agent-private','允许使用哪家模型服务？哪些数据不能发给外部模型（如手机号、证件号、金额）？是否必须本地或国内部署？');if(!answered(d.knowledge)||has(d.knowledge,'unsure'))ask('agent-knowledge','允许使用哪些知识源，哪些数据不能发给模型？','knowledge');if(has(d.agentJob,'tools')&&d.autonomy==='suggest')ask('agent-tools','已选择工具操作和只提供建议：是否仅生成操作方案？')}
 if(auto){if(!ai&&(!d.autonomy||d.autonomy==='unsure'))ask('auto-autonomy','自动化任务哪些可以直接执行，哪些需要人工确认？','autonomy');checks.push('核对重复触发、部分成功、重试与恢复，避免重复执行。');architecture.push('按触发方式设计任务状态与重试；仅在规模需要时引入独立队列。')}
 if(has(d.types,'admin')){checks.push('列表、详情与操作使用一致权限；走通录入到处理结果。');architecture.push('围绕业务对象和权限复用列表、表单与后台组件。')}
 if(has(d.types,'mini')){checks.push('核对微信授权返回、弱网与前后台切换；上传和真机可用分别验收。');architecture.push('划定小程序、后台 API 与微信平台的能力边界，多端共用业务规则。')}
 if(has(d.types,'desktop')||has(d.types,'mobile')){checks.push('在目标设备检查安装、更新和本地数据；有离线需求时验证恢复同步。');architecture.push('根据目标系统、离线与硬件需求选择原生、跨平台或封装方案。');if(!answered(d.platform))ask('platform','首版必须支持哪些设备与操作系统？','platform')}
 if(has(d.types,'web')){checks.push('检查响应式、关键交互和刷新恢复；内容型站点按需要检查搜索发现。');architecture.push(['brand','content'].includes(d.webFocus)?'先判断静态内容是否足够，只为必要业务引入服务端。':'按公开内容、登录和业务写入需求确定前后端边界。')}
 if(d.types.filter(t=>!['agent','automation','other'].includes(t)).length>1)checks.push('核对各端的保存、读取、状态和显示是否一致。');
 if(pay){checks.push('验证金额、重复提交、支付取消竞争及退款；按实际风险安排独立审核。');architecture.push('明确订单、交易与履约关系、幂等和历史快照。');if(!answered(d.paymentRules)||has(d.paymentRules,'unsure'))ask('payment-rules','支付、退款、超时与合并交易采用什么规则？','paymentRules')}
 const personal=has(d.data,'personal')||has(d.miniServices,'phone')||has(d.miniServices,'location');
 if(d.scope==='tenant'||has(d.data,'sensitive'))checks.push(d.depth==='prototype'?'原型阶段不使用真实的机密或个人数据。':'验证数据隔离和敏感信息边界，按上线要求核对审计与恢复。');
 if(personal&&d.depth!=='prototype')checks.push('个人信息只在必要处使用，核对页面展示、导出和日志中的脱敏。');
 if(pay&&(has(d.roles,'merchant')||['org','tenant'].includes(d.scope)))ask('payment-receiver','收款进哪个商户号？多门店或多商家是否需要分账？');
 if(legacy)checks.push('保护既有数据，验证兼容、迁移与恢复边界。');
 if(d.volume==='large'||d.growth==='high'){checks.push('以代表性数据和受限资源测量关键请求成本。');architecture.push('分开评估并发、历史数据、附件和外部调用成本，验证分页、索引与生命周期。')}
 if(d.depth==='prototype')checks.push('本轮确认流程与视觉；模拟服务不算真实集成证据。');
 if(d.depth==='demo')checks.push('核心数据持久化、演示环境可重置，明确真实与模拟服务。');
 if(d.depth==='launch')checks.push('绑定实际版本与环境，验证必要的备份恢复、真实集成与发布结果。');
 if(d.depth==='launch'&&d.integration==='mock')ask('mock-replace','哪些模拟服务必须在上线前替换为真实服务？');
 if(d.depth==='prototype'&&['real','mixed'].includes(d.integration))ask('prototype-real','交付目标是交互原型（数据可模拟），外部服务却选了真实接入：本轮是否真的要接入真实服务？');
 if(d.hosting==='local'&&(['real','mixed'].includes(d.integration)||has(d.knowledge,'web')||ai))ask('local-network','本机运行是否允许联网调用外部服务（包括大模型）？');
 if(has(d.features,'other'))ask('feature-other','首版功能里的「其他」具体是哪些功能？');
 if(d.customization==='custom')ask('custom-scope','「重点定制」具体指哪些部分？');
 if(d.deadline==='date')ask('deadline-date','「指定日期」是哪一天？有没有阶段性的日期？');
 if(d.roles.length>1&&!answered(d.roleRules))ask('role-rules','各角色分别能查看哪些数据、执行哪些操作？','roleRules');
 if(!answered(d.example))ask('example','给一份典型输入及预期结果，作为开发与验收样例。','example');
 if(!answered(d.rules)&&(pay||has(d.features,'booking')||has(d.features,'approval')))ask('rules','数量口径、状态变化、取消和异常分别按什么规则处理？','rules');
 if(!answered(d.flow))delegate('由 AI 提出主流程草案，开工前与人核对。','flow');
 if(!answered(d.acceptance))delegate('由 AI 提出验收场景，明确完成标准。','acceptance');
 if(has(d.types,'other'))delegate('根据业务目标推荐产品形态，开工前与人确认。','types');
 if(!d.depth||d.depth==='advice')delegate('推荐本轮交付范围与取舍，开工前与人确认。','depth');
 // 需要人准备的账号与资料：AI 无法代办，只在要真实接入时列出。
 const real=d.depth==='launch'||['real','mixed'].includes(d.integration),maybe=!real&&d.integration==='unsure'&&hasExternal(d),start=prepare.length;
 if(ai&&(real||d.depth==='demo'))prepare.push('大模型服务的账号与 API Key（或指定使用哪家模型服务）。');
 else if(ai&&d.depth==='prototype')prepare.push('如果原型使用真实模型：大模型服务的账号与 API Key。');
 if(real||maybe){
  if(has(d.types,'mini'))prepare.push(d.depth==='launch'?'微信小程序 AppID、完成认证的小程序主体；上线前还需完成小程序备案。':'微信小程序 AppID，以及完成认证的小程序主体。');
  if(pay)prepare.push(has(d.types,'mini')?'微信支付商户号，以及支付配置权限。':'支付服务的商户账号（如微信支付、支付宝）。');
  if(has(d.features,'notifications'))prepare.push('消息通道账号（短信、邮件或订阅消息模板）。');
  if(has(d.features,'sync'))prepare.push('需要联动的第三方系统的接口文档与测试账号。');
  if(d.distribution==='store')prepare.push('应用商店开发者账号（如 Apple、Google）。');
  if(['cloud','existing','private'].includes(d.hosting))prepare.push('服务器或云平台账号，以及部署权限。');
  else if(d.depth==='launch'&&d.hosting!=='local')prepare.push('服务器或云平台账号，以及部署权限（具体平台在确定运行环境后再定）。');
  if(d.depth==='launch'&&d.hosting!=='local'&&(has(d.types,'web')||has(d.types,'mini')||has(d.types,'admin')))prepare.push('正式域名；面向中国大陆公开访问时，还需完成 ICP 备案。');
  if(d.depth==='launch'&&personal)prepare.push(has(d.types,'mini')?'隐私政策与用户协议，以及小程序服务类目所需的资质。':'隐私政策与用户协议（由运营主体确认）。');
  if(hasExternal(d)&&prepare.length===start)prepare.push('真实接入所需的平台账号、资质或接口。');
  if(maybe)for(let i=start;i<prepare.length;i++)prepare[i]='如需真实接入：'+prepare[i];
 }
 if(prepare.length&&d.accessReady==='ready')prepare.unshift('你已表示平台账号与接口已就绪：开工后按需提供给 AI。');
 if(legacy&&!answered(d.resources))prepare.push('现有代码、接口文档或数据样本的位置，用于核对可复用部分。');
 const participation={milestones:'关键节点集中看结果，常规实现自主推进。',visual:'先看样板与原型，再扩展；必要的技术验证可先行。',delegate:'助手推荐方案，用户决定实质业务取舍和新增授权。',together:'方案阶段一起讨论有效选项，确认后集中实现。'};
 const priorities={speed:'先交付最小有用闭环，保留必要验证，延期功能单列。',visual:'先验证样板与真实形态内容，再复用设计。',business:'优先角色与数据之间的完整流转。',reliability:'先验证高后果失败路径，再扩展功能。',cost:'先估算部署、存储和外部调用成本。'};
 return {mode,first,you:['业务目标、关键规则与范围取舍','回答待决定事项，准备账号与资料','每个阶段结束后验收'],agent:['架构、实现与相关验证','按阶段交付，说明限制','交给 AI 的事项：给出选择与理由'],checks:[...new Set(checks)],architecture:[...new Set(architecture)],questions,delegated,prepare:[...new Set(prepare)],phases,participation:participation[d.collaboration]||'按项目风险设置确认点，普通实现细节由助手负责。',priority:priorities[d.priority]||(d.priority==='suggest'?'首要目标由 AI 推荐，开工前与人确认。':'默认交付效率优先，复用成熟方案，保留必要业务验证。')};
}
// 分阶段交付：每个阶段写清做什么、人怎么验收；AI 做完一个阶段就停下等验收。
function phasesFor(d,ai,auto,legacy){
 const core={title:'核心流程贯通',do:'先做通一条完整业务链路：界面、接口与数据保存贯通，再扩展其他功能。',check:'按验收场景走通核心流程，刷新或重启后数据仍在。'};
 const plans={
  prototype:[{title:'代表页面与主流程',do:'按页面清单做出代表性页面，串起可点击的核心流程，数据可模拟。',check:'人按核心流程点一遍，确认流程与视觉。'},{title:'补齐页面与状态',do:'补齐其余页面，以及空数据、加载中、出错等状态。',check:'逐页对照页面清单核对。'}],
  demo:[core,{title:'首版功能补齐',do:'补齐其余首版功能与角色权限。',check:'按验收场景逐条核对，各角色分别走一遍。'},{title:'演示准备',do:'准备演示数据，演示环境可重置，标明哪些服务是模拟的。',check:'人完整演示一遍。'}],
  launch:[core,{title:'功能与权限补齐',do:'补齐其余首版功能、角色权限与异常处理。',check:'按验收场景逐条核对，包括失败和越权的情况。'},...(hasExternal(d)?[{title:'真实服务接入',do:'把模拟服务替换为真实接入，处理失败、超时与重试。',check:'用真实账号在测试环境验证，模拟通过不算数。'}]:[]),{title:'上线验证',do:'部署到正式环境，验证备份恢复与发布结果。',check:'人在正式环境走一遍验收场景，再决定是否对外发布。'}]};
 let list=plans[d.depth]||[{title:'范围与方案确认',do:'先提出本轮交付范围、取舍与分阶段计划，不写代码。',check:'人确认范围与计划后再开工。'}];
 if(d.depth==='demo'||d.depth==='launch'){
  if(ai)list=[{title:'Agent 效果验证',do:'先用典型输入验证效果、失败处理与工具边界，保留失败样本。',check:'人查看样例输入与输出，确认效果可接受。'},...list];
  else if(auto)list=[{title:'单项任务跑通',do:'先跑通一项任务，验证重复触发、重试与中断恢复。',check:'人触发一次任务并核对结果。'},...list];
 }
 if(legacy)list=[{title:'现有系统核对',do:'核对现有系统必须保留的行为与数据，此阶段不改动现有数据。',check:'人确认需要保留的行为清单。'},...list];
 return list;
}
// 选项里「待推荐 / 待估」表示交给 AI 推荐或估算；「待定 / 待确认」表示人还没决定。
export const AI_DECIDES='交给 AI 决定';
export function intentOf(id,v){const opt=fields.find(f=>f.id===id)?.options?.find(x=>x.value===v);if(!opt)return null;return /(^|\/ )待(推荐|估)$/.test(opt.label)?'ai':/(^|\/ )待(定|确认)$/.test(opt.label)?'open':null}
// 答复写的是「待定 / 还没想好」之类，仍算没有决定。
const UNDECIDED=/^(待定|待确认|未定|还没定|不确定|没想好|还没想好|不知道)[。.!！?？…]*$/;
// 人机交接：把表单整理成「人已决定 / 交给 AI / 还没决定 / 需要人准备 / 开发阶段」。
export function handoff(raw){const d={...blank(),...activeData(raw)},p=recommend(raw),answers=raw.answers||{};
 const covered=new Set([...p.questions,...p.delegated].map(x=>x.covers).filter(Boolean)),questions=[...p.questions],decided=[],ai=p.delegated.map(x=>x.text);
 for(const f of fields){if(!visible(f,d)||!answered(d[f.id]))continue;const values=Array.isArray(d[f.id])?d[f.id]:[d[f.id]],chosen=values.filter(v=>!intentOf(f.id,v)),pending=values.filter(v=>intentOf(f.id,v)),intent=pending.map(v=>intentOf(f.id,v))[0];
  // 文本和具体选项算人已决定；多选里同时带「待X」时，具体选项照样保留。
  if(chosen.length)decided.push({label:f.label,value:label(f.id,Array.isArray(d[f.id])?chosen:chosen[0])});
  if(!intent||covered.has(f.id))continue;
  if(intent==='ai')ai.push(`${f.label}：${chosen.length?'其余':''}由 AI ${label(f.id,pending[0])==='待估'?'估算，开工前与人确认':'推荐'}`);
  else questions.push({id:'field:'+f.id,label:f.label,covers:f.id,q:chosen.length?`${f.label}：除已选的「${label(f.id,chosen)}」外，其余还没定，请定下来。`:`${f.label}还没定（当前选了「${label(f.id,pending[0])}」），请定下来。`})}
 const settled=[],open=[];
 for(const q of questions){const a=typeof answers[q.id]==='string'?answers[q.id].trim():'';if(!a||UNDECIDED.test(a))open.push(q);else if(a===AI_DECIDES)ai.push(q.label?`${q.label}：人已交给 AI 决定`:`${q.q}（人已交给 AI 决定）`);else settled.push({...q,answer:a})}
 return {decided,settled,ai:[...new Set(ai)],open,questions,prepare:p.prepare,phases:p.phases,ready:!validate(raw).length&&!open.length}}
export const AI_RULES=['开工前读取适用的项目规则（如 AGENTS.md；可用时还有 focused-delivery 技能）；先复述目标，列出实施计划，以及你对「交给 AI 决定」各项的选择和理由，等人确认后再写代码。','「人已决定」是约束，不得擅自更改；发现矛盾或做不到时，停下来说明原因并给出可选方案。','「还没决定」的事项不得自行假设，开工前集中提问。','表单里没填的选填项：普通、可逆的实现细节自行处理，并在计划中说明；会改变业务、成本、权限或范围的，先问人。没填不代表同意或不需要。','按开发阶段推进。每个阶段结束后停下，汇报完成内容和验证证据（截图、测试结果、可访问的地址），等人验收后再继续。','需要人准备的账号、密钥和资料，用到时再向人索取；密钥只配置在运行环境里，不写进代码、对话或简报。','本简报不授权真实交易、对外发送消息、删除数据、公开发布，也不授权把业务数据或个人信息发送给外部模型或第三方服务；这些操作必须单独获得人的确认。'];
const safe=v=>String(v).split('\n').map(l=>l.replace(/^(\s*)(#|>|```|[-*_]{3,}\s*$)/,'$1\\$2')).join('\n'),indent=v=>safe(v).replace(/\n/g,'\n  '),item=(k,v)=>`- ${k}：${indent(v)}`;
export function brief(d){const p=recommend(d),h=handoff(d),missing=validate(d).length;
 const status=missing?'草稿，必填信息未完成':h.open.length?`还有 ${h.open.length} 项待人决定，AI 开工前须先问清`:'人已完成决定，可以交给 AI';
 return [`# ${d.name.trim()||'未命名项目'} · 项目简报`,'',`状态：${status}`,'','本简报分清人已决定、交给 AI 决定和还没决定的事项。AI 按文末「给 AI 的工作规则」执行。',
 '','## 人已决定',...h.decided.map(x=>item(x.label,x.value)),...h.settled.map(x=>x.label?item(x.label,x.answer):`- ${x.q}\n  决定：${indent(x.answer)}`),
 '','## 交给 AI 决定',...(h.ai.length?h.ai:['无，全部事项由人决定。']).map(s=>'- '+s),
 '','## 还没决定（AI 开工前须先问人）',...(h.open.length?h.open.map(x=>'- '+x.q):['- 无。']),
 '','## 需要人准备',...(h.prepare.length?h.prepare:['无。']).map(s=>'- '+s),
 '','## 开发阶段（每个阶段结束后停下，等人验收）',...h.phases.flatMap((x,i)=>[`${i+1}. ${x.title}`,`   - 做什么：${x.do}`,`   - 怎么验收：${x.check}`]),
 '','## 协作建议（由选项生成，尚非批准方案）',`- 方式：${p.mode}`,`- 首份成果：${p.first}`,`- 参与方式：${p.participation}`,`- 优先级：${p.priority}`,
 '','## 架构判断方向',...(p.architecture.length?p.architecture:['先结合目标确认业务关系、数据和运行边界。']).map(s=>'- '+s),
 '','## 验证关注点',...p.checks.map(s=>'- '+s),
 '','## 给 AI 的工作规则',...AI_RULES.map((s,i)=>`${i+1}. ${s}`)].join('\n')}

export function stepProgress(d,step){const fs=fields.filter(f=>f.step===step&&visible(f,d));return {filled:fs.filter(f=>answered(d[f.id])).length,total:fs.length}}
