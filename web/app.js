const app = document.querySelector('#app');
const nav = document.querySelector('#navigation');
const names = {buyer:'Клиент', guest:'Гость', manager1:'Менеджер 1', manager2:'Менеджер 2', developer:'Разработчик', assistant:'Помощник', system:'Система', open:'Открыт', queued:'В очереди', assigned:'Назначен', resolved:'Закрыт', draft:'Черновик', published:'Опубликован', archived:'Архив', retired:'Архив', rules:'Сценарный ответ', local:'Локальная модель', hybrid:'Помощник с моделью', answer:'Ответ', quote:'КП', invoice:'Счёт', proposal:'КП'};
const label = value => Object.hasOwn(names,value) ? names[value] : value ?? '';
const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[char]);
const attr = escape;
const format = new Intl.NumberFormat('ru-RU');
const num = value => value == null || value === '' ? 'По запросу' : format.format(value);
const list = value => Array.isArray(value) ? value : Object.values(value ?? {});
const safeUrl = value => {
  try { const url = new URL(value); return /^https?:$/.test(url.protocol) ? url.href : ''; }
  catch { return ''; }
};
let token = sessionStorage.getItem('softline-token') ?? '';
let telegramSession = sessionStorage.getItem('softline-telegram-session');
let telegramEnabled = false;
let session, role, state, dialogId, result, tab, busy = false, samplePage = 0, sampleQuery = '', checkoutOffer;
let errorText = '';
let catalogSearch;
const clientContexts = new Map();
const sample = (offers, count) => {
  const pool = [...offers];
  return Array.from({length:Math.min(count,pool.length)}, () => pool.splice(Math.floor(Math.random()*pool.length),1)[0]);
};
const quantityOf = offer => offer.min_quantity || 1;
function clientContext() {
  const key = session.role + '/' + state.buyer_id;
  if (!clientContexts.has(key)) {
    const offers = list(state.offers);
    const priced = offers.filter(offer => offer.available);
    clientContexts.set(key, {
      viewed: undefined,
      cart: new Map((role==='buyer' ? sample(priced,1+Math.floor(Math.random()*2)) : [])
        .map(offer => [String(offer.id),{offer,quantity:quantityOf(offer)}])),
    });
  }
  return clientContexts.get(key);
}
let buyerMode;
const storefront = () => role==='buyer' || role==='guest';
function guestToken() {
  let value=sessionStorage.getItem('softline-guest');
  if (!value) {
    const bytes=crypto.getRandomValues(new Uint8Array(32));
    value='guest.'+Array.from(bytes,n=>n.toString(16).padStart(2,'0')).join('');
    sessionStorage.setItem('softline-guest',value);
  }
  return value;
}
let chatOpen = false, launcherSeen = false, training = false, buyerDraft = '';
let chatPane='', quoteDraft, fileDraft, referenceDraft, staffDraft='', audioContext;
let soundMuted=localStorage.getItem('softline-chat-muted')==='true';
const clearComposer=()=>{ buyerDraft=staffDraft=''; quoteDraft=fileDraft=referenceDraft=undefined; };
const chatIcons={
  close:'<path d="m6 6 12 12M18 6 6 18"/>',
  bell:'<path d="M18 9a6 6 0 0 0-12 0c0 6-3 7-3 9h18c0-2-3-3-3-9ZM10 21h4"/>',
  power:'<path d="M12 2v10M7 5a9 9 0 1 0 10 0"/>',
  more:'<path d="M12 5h.01M12 12h.01M12 19h.01" stroke-width="3.5"/>',
  reply:'<path d="M9 5 3 11l6 6v-4h4c4 0 6 2 8 5 0-7-3-9-8-9H9Z"/>',
  attach:'<path d="m8 13 6-6a3 3 0 0 1 4 4l-8 8a5 5 0 0 1-7-7l9-9m-5 9a1 1 0 0 0 2 2l7-7"/>',
  emoji:'<circle cx="12" cy="12" r="9"/><path d="M8 14c2 3 6 3 8 0M9 9h.01M15 9h.01"/>',
  question:'<path d="M20 11a8 8 0 0 1-8 8H4l2-4a8 8 0 1 1 14-4Z"/><path d="M10 9a2 2 0 1 1 3 1.7c-.7.5-1 1-1 1.8m0 2.5h.01"/>',
  cart:'<path d="M2 4h3l2 11h11l3-8H6"/><circle cx="9" cy="19" r="1"/><circle cx="17" cy="19" r="1"/>',
  catalog:'<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
  payments:'<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 10h18m-14 5h4"/>',
  keys:'<circle cx="15.5" cy="8.5" r="4.5"/><path d="m12 12-7 7H3v-3l7-6m7-2h.01"/>',
  copy:'<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h2"/>',
  download:'<path d="M12 3v12m-4-4 4 4 4-4M4 16v4h16v-4"/>',
  external:'<path d="M13 4h7v7m0-7-9 9m7 0v6H4V6h7"/>',
  refresh:'<path d="M20 10a8 8 0 1 0-1 7m1-13v6h-6"/>',
  logout:'<path d="M11 4H4v16h7m3-12 4 4-4 4m-5-4h10"/>',
  plus:'<path d="M12 5v14M5 12h14"/>',
  minus:'<path d="M5 12h14"/>',
  send:'<path d="m3 10 18-7-7 18-3-8-8-3Zm8 3 10-10"/>'
};
const icon=name=>'<svg aria-hidden="true" viewBox="0 0 24 24">'+chatIcons[name]+'</svg>';
const iconButton=(name,title,action,value='',extra='')=>'<button type="'+(action?'button':'submit')+'" class="chat-icon '+extra+'" aria-label="'+attr(title)+'" title="'+attr(title)+'" '+
  (action?'data-do="'+action+'" data-value="'+attr(value)+'"':'')+
  (action==='chat-options'?' aria-controls="chat-options" aria-expanded="'+(chatPane==='options')+'"':'')+'>'+icon(name)+'</button>';
const chatTime=new Intl.DateTimeFormat('ru-RU',{hour:'2-digit',minute:'2-digit',timeZone:'Europe/Moscow'});
const displayText=value=>String(value ?? '').replace(/\bO&(?:amp;)+O\b/gi,'O&O');
const timeMarkup=at=>at && Number.isFinite(Date.parse(at))?'<time datetime="'+attr(at)+'">'+chatTime.format(new Date(at))+'</time>':'';
function notifyMessage() {
  if (soundMuted || !audioContext || audioContext.state!=='running') return;
  const sound=audioContext.createOscillator(), volume=audioContext.createGain(), start=audioContext.currentTime;
  sound.frequency.value=660;
  volume.gain.setValueAtTime(.055,start);
  volume.gain.exponentialRampToValueAtTime(.001,start+.15);
  sound.connect(volume).connect(audioContext.destination);
  sound.start(start);
  sound.stop(start+.15);
}
const modes = [['rules','По сценарию'],['local','Локальная модель'],['hybrid','Модель и каталог']];
function resetWorkspace() {
  telegramOpen=false;
  chatOpen = launcherSeen = training = false;
  clearComposer();
  chatPane='';
  checkoutOffer=undefined;
  catalogSearch=undefined;
}
const requestIds = new WeakMap();
const button = (text, action, value, extra='') => '<button type="button" data-do="'+action+'" data-value="'+attr(value)+'" '+extra+'>'+escape(text)+'</button>';
const field = (title,name,value='',extra='') => '<label>'+title+'<br><input name="'+name+'" value="'+attr(value)+'" '+extra+'></label>';
const area = (title,name,value='',extra='') => '<label>'+title+'<textarea name="'+name+'" rows="3" '+extra+'>'+escape(value)+'</textarea></label>';
const submit = text => '<button class="primary">'+text+'</button>';
const hidden = (name,value) => '<input type="hidden" name="'+name+'" value="'+attr(value)+'">';
const form = (action,html) => '<form data-action="'+action+'" class="form stack">'+html+'</form>';
const choose = (name, options, active) => '<select name="'+name+'">'+options.map(([id,text]) => '<option value="'+attr(id)+'" '+(id===active?'selected':'')+'>'+escape(text)+'</option>').join('')+'</select>';
const rows = (items,render,empty) => items.length ? items.map(render).join('') : '<p class="muted">'+empty+'</p>';
const item = (title,detail) => '<div class="item"><h3>'+escape(title)+'</h3>'+detail+'</div>';
const panel = (title,detail) => '<section class="panel"><h2>'+escape(title)+'</h2>'+detail+'</section>';
const choices = (items, action, active) => '<div class="row">'+items.map(([id,text]) => button(text,action,id,'aria-pressed="'+(active===id)+'"')).join('')+'</div>';
const stats = entries => '<dl class="summary">'+entries.map(([key,value])=>'<div><dt>'+escape(key)+'</dt><dd>'+escape(value)+'</dd></div>').join('')+'</dl>';
const activeDialog = () => list(state.dialogs).find(d=>String(d.id)===String(dialogId));
const messageRole = m => m.role ?? m.author;

async function request(path, data) {
  let response;
  try {
    response = await fetch(path, {method:data?'POST':'GET',cache:'no-store',headers:{
      Authorization:'Bearer '+token, ...(data?{'Content-Type':'application/json'}:{}),
      ...(telegramSession ? {'X-Stand-Session':telegramSession} : {}),
      ...(session?.role==='jury' && (data?.role==='guest' || new URL(path,location.href).searchParams.get('role')==='guest')
        ? {'X-Stand-Guest':guestToken()} : {})
    },body:data?JSON.stringify(data):undefined,signal:AbortSignal.timeout(25000)});
  } catch { throw Error('Связь со стендом прервалась. Проверьте соединение и повторите.'); }
  const payload = await response.json().catch(()=>({}));
  if (response.status===401) {
    sessionStorage.removeItem('softline-token');
    sessionStorage.removeItem('softline-telegram-session');
    telegramSession = undefined;
    telegramEnabled = false;
    token = '';
    session = undefined;
    state=role=result=dialogId=tab=undefined;
    throw Error('Проверьте токен и войдите снова.');
  }
  if (!response.ok) {
    const error=Error(response.status===403?'У этой роли нет доступа к действию.':payload.error || 'Не удалось выполнить запрос. Попробуйте ещё раз.');
    error.status=response.status;
    throw error;
  }
  return payload;
}
async function roleState(nextRole) {
  if ((nextRole==='manager1' || nextRole==='manager2') && !telegramSession) {
    telegramSession = crypto.randomUUID();
    sessionStorage.setItem('softline-telegram-session',telegramSession);
  }
  const next = await request('/api/state?role='+encodeURIComponent(nextRole));
  telegramEnabled ||= Boolean(next.telegram);
  return next;
}
async function loadState() { state = await roleState(role); buyerMode ??= state.default_model_mode || 'local'; }
async function loadCatalog(query, page) {
  const data = await request('/api/action',{role,action:'search',payload:{query,by_product:true,page,limit:8}});
  catalogSearch={query,page:data.page ?? page,offers:list(data.offers),hasMore:data.has_more===true};
}
async function signIn(value) {
  token = value;
  session = await request('/api/me');
  role = session.roles?.[0] ?? session.role;
  const pending=sessionStorage.getItem('softline-guest-dialog');
  const visitor=sessionStorage.getItem('softline-guest');
  if (pending && visitor && ['buyer','personal'].includes(session.role)) {
    try {
      await request('/api/action',{role,action:'continue_guest_chat',payload:{guest_token:visitor,dialog_id:pending}});
      dialogId=pending;
      chatOpen=true;
    } catch (error) {
      if (![403,404].includes(error.status)) {
        session=state=undefined;
        throw error;
      }
      dialogId=undefined;
      errorText='Этот чат уже недоступен. Можно начать новый разговор.';
    }
    sessionStorage.removeItem('softline-guest-dialog');
  }
  await loadState();
  sessionStorage.setItem('softline-token',token);
}
async function act(action,payload) {
  const previous=activeDialog();
  result = await request('/api/action',{role,action,payload});
  if (result.dialog) dialogId = result.dialog.id;
  if (action==='download') {
    const url = URL.createObjectURL(new Blob([result.content],{type:result.content_type}));
    const anchor = document.createElement('a');
    anchor.href=url;
    anchor.download=result.filename;
    anchor.click();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
  } else {
    await loadState();
    if (action==='message' && chatPane==='continue') chatPane='';
    if (result.dialog?.outcome && !(previous?.id===result.dialog.id && previous.outcome) &&
        ['buyer','guest'].includes(role)) showDialogResult(result.dialog);
  }
}
function showDialogResult(d) {
  const popup=document.createElement('dialog');
  popup.className='dialog-result';
  popup.setAttribute('aria-label','Итоги диалога');
  const metrics=d.response_metrics;
  const names={nimble:'Nimble',local_llm:'Локальная LLM',jev:'Jev',cloud:'Облачная LLM'};
  const tokens=Object.entries(metrics?.usage ?? {}).map(([key,v])=>
    '<li>'+escape(names[key] ?? key)+': '+v.input_tokens+' входящих, '+v.output_tokens+' исходящих'+
    (v.calls>v.metered_calls?' (часть вызовов без показаний)':'')+'</li>').join('');
  popup.innerHTML='<h2>Итоги диалога</h2><p>'+(d.outcome==='handoff'?'Передано менеджеру':'Вопрос решён с помощником')+'</p>'+
    (d.night_demo?'<p>Время в демо: '+escape(d.night_demo)+' МСК. Смена начнётся в 09:00.</p>':'')+
    (metrics?'<p>Время подготовки ответов: '+(metrics.ms<1000?metrics.ms.toLocaleString('ru-RU')+' мс':
    (metrics.ms/1000).toLocaleString('ru-RU',{maximumFractionDigits:3})+' с')+'</p>'+
    (tokens?'<h3>Токены по моделям</h3><ul>'+tokens+'</ul>':'<p>Токены моделей: 0</p>'):'<p>Замеры для этого обращения отсутствуют.</p>')+
    '<form method="dialog"><button autofocus>Закрыть</button></form>';
  document.body.append(popup);
  popup.addEventListener('close',()=>popup.remove(),{once:true});
  popup.showModal();
}
async function downloadChatFile(messageId) {
  const file=await request('/api/action',{role,action:'chat_file',payload:{dialog_id:dialogId,message_id:messageId}});
  const bytes=Uint8Array.from(atob(file.content),letter=>letter.charCodeAt(0));
  const url=URL.createObjectURL(new Blob([bytes],{type:'application/octet-stream'}));
  const link=document.createElement('a');
  link.href=url;
  link.download=file.filename;
  link.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function renderLogin() {
  nav.replaceChildren();
  resetWorkspace();
  app.classList.remove('buyer-main','chat-visible');
  app.innerHTML = standHeading('Поддержка Softline')+'<section class="panel narrow"><h2>Вход</h2>'+statusMarkup()+form('login',
    field('Токен доступа','token','','type="password" required autocomplete="off" autofocus')+
    '<div class="login-actions">'+submit('Войти')+button('Продолжить как гость','guest-start','')+'</div>')+'</section>';
  syncMobileChat();
  applyFieldHints();
}
function statusMarkup() {
  return '<p id="status" class="status" role="status" tabindex="-1">'+escape(errorText)+'</p>';
}
function standHeading(title) {
  return '<div class="stand-heading"><h1>'+escape(title)+'</h1></div>';
}
function render() {
  if (!session) { renderLogin(); return; }
  const visibleRoles=list(session.roles).filter(id=>id!=='manager2' || id===role || state?.manager2_active);
  nav.innerHTML = (session.role==='jury' ? choices(visibleRoles.map(id=>[id,label(id)]),'role',role) : '')
    +(!storefront() || session.role==='jury' ? iconButton('refresh','Обновить обращения','refresh') : '')
    +(session.role==='guest'?button('Войти в кабинет','customer-login',''):iconButton('logout','Выйти','logout'));
  if (!state) { app.innerHTML='<h1>Рабочее место</h1>'+statusMarkup(); return; }
  const page = storefront() ? buyerPage() : staffPage();
  app.classList.toggle('buyer-main',storefront());
  app.innerHTML = standHeading(page.title)+statusMarkup()+page.html;
  syncMobileChat();
  applyFieldHints();
}
const phoneScreen=matchMedia('(max-width:540px)');
function syncMobileChat() {
  const widget=app.querySelector('.widget');
  app.classList.toggle('chat-visible',Boolean(widget));
  const fullScreen=Boolean(widget && phoneScreen.matches);
  for (const node of [document.querySelector('header'),...app.children]) {
    if (node!==widget) node.inert=fullScreen;
  }
  if (widget) {
    widget.setAttribute('role',fullScreen?'dialog':'region');
    if (fullScreen) widget.setAttribute('aria-modal','true');
    else widget.removeAttribute('aria-modal');
  }
}
phoneScreen.addEventListener('change',syncMobileChat);
const hints = {
  token:'Вставьте токен для своей роли.', query:'Введите название программы или слова для поиска.',
  text:'Введите сообщение собеседнику.',
  quantity:'Количество лицензий для расчёта.', discount:'Процент скидки для счёта или коммерческого предложения.',
  label:'Короткая тема, по которой можно найти похожие вопросы.', buyer_type:'Например, организация или частное лицо.',
  license_term:'На какой период нужна лицензия.', delivery_term:'Когда клиенту нужна поставка.',
  note:'Комментарий доступен сотрудникам.', manual_review:'Отметить для проверки сотрудником.',
  collect_sample:'Включить этот диалог в обучающую выборку.', manager_id:'Кому передать этот чат.',
  name:'Название шаблона в списке.', kind:'Для какого документа или ответа применяется шаблон.'
};
function applyFieldHints() {
  app.querySelectorAll('label input,label select,label textarea').forEach(control=>{
    const action=control.form?.dataset.action;
    const hint=({template_draft:'Введите текст, который будет подставлен из шаблона.',
      template_emulate:'Введите пример вопроса для проверки шаблона.',reply:'Этот ответ получит клиент.'})[action];
    const description=action==='login' ? '' : (control.name==='text' && hint) || hints[control.name];
    if (description) control.setAttribute('aria-description',description);
  });
}
function conversation(dialog,staff=false) {
  if (!dialog) return '<p class="muted">'+(staff?'Выберите обращение в очереди.':'Задайте вопрос о товаре, заказе или документах.')+'</p>';
  return '<div aria-label="Переписка">'+rows(list(dialog.messages), m => {
    const who = messageRole(m);
    const speaker = who==='buyer' && (role==='guest' || dialog.buyer_id?.startsWith('visitor-')) ? 'guest' : who;
    const id = m.id ?? m.message_id;
    const offers=list(m.offers).slice(0,4);
    const legacyOffer=who==='assistant' && offers.length===1 && offers[0];
    const legacyText=legacyOffer && [legacyOffer.description || legacyOffer.name, 'Что хотите уточнить о покупке?'].join('\n\n');
    const text=legacyOffer && (m.text===legacyText || m.text==='Что хотите уточнить по лицензии?')?'':m.text;
    const quote=m.quote?'<blockquote><b>'+escape(label(m.quote.role))+'</b><div>'+escape(displayText(m.quote.text))+'</div></blockquote>':'';
    const file=m.attachment?button(m.attachment.name,'chat-file',id,'class="chat-file"'):'';
    const chatDocument=staff || !m.document_id?undefined:list(state.documents).find(d=>d.id===m.document_id);
    const download=chatDocument?button(chatDocument.kind==='proposal'?'Скачать КП':'Скачать счёт','download',chatDocument.id):'';
    const reference=m.reference?'<div class="message-reference">'+icon('attach')+escape(displayText(m.reference.label))+'</div>':'';
    const reply=id && dialog.status!=='resolved' && !training && role!=='developer' && who!=='system'?iconButton('reply','Ответить на сообщение','quote',id):'';
    return '<article class="bubble '+(who === (staff?role:'buyer')?'mine':'')+'"><small>'+escape(label(speaker))+'</small>'+quote+
      reference+'<div class="message-text" data-message="'+attr(id)+'">'+escape(displayText(text))+'</div>'+file+download+
      offers.slice(0,1).map(o=>chatOffer(o,staff || dialog.status==='resolved',m.show_offer_details)).join('')+
      (offers.length>1?'<details><summary>Ещё варианты ('+(offers.length-1)+')</summary>'+offers.slice(1).map(o=>chatOffer(o,staff || dialog.status==='resolved')).join('')+'</details>':'')+
      (safeUrl(m.source)?'<a href="'+attr(safeUrl(m.source))+'" target="_blank" rel="noreferrer">Источник</a>':'')+
      (staff && training && id && (who==='assistant'||who==='bot')?
        '<div class="row">'+button('+' ,'rate',id,'aria-label="Ответ полезен" data-rating="+"')+button('−','rate',id,'aria-label="Ответ не помог" data-rating="-"')+'</div>':'')+
      (m.synthetic ? '<span class="mock-data-note">исп. синтетич. данные</span>' : '')+
      '<div class="message-meta">'+timeMarkup(m.at)+reply+'</div></article>';
  },'Сообщений пока нет.')+'</div>';
}
function offerPrice(offer, showComparison = true) {
  if (!(offer.price>0)) return 'Цена по запросу';
  const current=escape(num(offer.price))+' '+escape(offer.currency);
  return showComparison && Number(offer.old_price)>Number(offer.price) && Number(offer.price)>0
    ? '<span class="reduced-price">'+current+' <span>Цена снижена</span></span> <del>'+escape(num(offer.old_price))+' '+escape(offer.currency)+'</del>' : current;
}
function offerAction(o, action) {
  if (o.group_recommendation && o.available && !clientContext().cart.has(String(o.id))) {
    return button('В корзину','chat-cart',o.id);
  }
  if (role==='guest' && o.price>0) return button('Войти для оформления','customer-login','');
  return o.price>0 ? button('Счёт или КП',action,o.id,'data-available="true"') : button('Запросить цену','price-request',o.id);
}
function chatOffer(o,staff,details) {
  const price=details===false && !o.old_price?'':'<p>'+offerPrice(o)+'</p>';
  return item(displayText(o.name).replace(/[,;]\s*$/,''),price+quantityTerms(o)+offerPreview(o,details)+(staff?'':offerAction(o,'chat-offer')));
}
function offerPreview(o,expanded) {
  const url=safeUrl(o.url);
  const {fields,description}=offerDetails(o);
  const facts=fields.length?'<dl>'+fields.map(([name,value])=>'<div><dt>'+escape(name)+'</dt><dd>'+escape(value)+'</dd></div>').join('')+'</dl>':'';
  const link=url?'<a href="'+attr(url)+'" target="_blank" rel="noreferrer">Открыть на сайте</a>':'';
  if (!description && fields.length<=1) return '<div class="offer-inline">'+facts+link+'</div>';
  return '<details class="offer-preview"'+(expanded?' open':'')+'><summary>Характеристики</summary><div>'+facts+
    (description?'<p>'+escape(description)+'</p>':'')+link+'</div></details>';
}
function quantityRange(o) {
  const min=quantityOf(o), max=o.max_quantity;
  if (min===max) return num(min)+' шт.';
  return 'От '+num(min)+(max?' до '+num(max):'')+' шт.';
}
function quantityTerms(o) {
  const capacity=o.params?.['К-во пользователей до']?.find(v=>/^\d+$/.test(v));
  const units=o.min_quantity ? 'Заказ '+quantityRange(o).toLocaleLowerCase('ru-RU') : '';
  return [capacity?'До '+num(Number(capacity))+' пользователей':null,units].filter(Boolean).map(text=>'<p>'+text+'</p>').join('');
}
function offerDetails(offer) {
  const labels=new Map([['Срок действия','Срок лицензии'],['Платформа','Платформа'],['Тип лицензии','Тип лицензии'],
    ['Тип поставки','Поставка'],['Минимальная покупка','Минимальный заказ']]);
  const values=new Map();
  const description=[];
  const useful=value=>value && !/^(не определено|другое или отсутствует)$/i.test(value);
  for (const part of displayText(offer.description ?? '').split(/\s*•\s*|\n+/)) {
    const line=part.trim();
    if (!line || line===displayText(offer.name) || (offer.min_quantity && /^(минимальная покупка[\s:]|заказ от\s)/i.test(line))) continue;
    const [,key,value]=line.match(/^([^:]+):\s*(.+)$/) ?? [];
    if (labels.has(key)) { if (useful(value)) values.set(key,value); }
    else description.push(line);
  }
  const fields=[];
  for (const [key,label] of labels) {
    const paramKey=key==='Платформа'?'Совместимая архитектура/платформа':key;
    const params=list(offer.params?.[paramKey]).map(displayText).filter(useful);
    const value=values.get(key) || [...new Set(params)].join(', ');
    if (value && !(key==='Минимальная покупка' && offer.min_quantity)) fields.push([label,value]);
  }
  return {fields,description:[...new Set(description)].join(' ')};
}
function buyerPage() {
  const d = activeDialog();
  const current = tab ?? 'catalog';
  const tabs = [['catalog','Каталог'],['cart','Корзина'],...(role==='buyer'?[['payments','Счета и платежи'],['keys','Дистрибутивы и ключи']]:[])];
  const modules = {catalog:catalogPage,cart:cartPage,payments:paymentsPage,keys:keysPage};
  const jury = session.role==='jury';
  const settings = jury && role==='buyer' ? '<aside class="panel session" id="jury-stand"><h2>Настройки сессии</h2>'+
    '<label>Профиль клиента'+choose('client_profile',Object.entries(state.client_profiles ?? {}),state.buyer_id)+'</label>'+
    '<label>Модель ответа'+choose('mode',modes,buyerMode)+'</label>'+
    button('Очистить историю','clear-history','')+'</aside>' : '';
  const owner=state.customer?.company_name || state.customer?.full_name;
  const store = '<div class="shop">'+(owner && !settings?'<p>'+escape(owner)+'</p>':'')+'<h2 id="context-heading">Контекст переписки</h2>'+
    '<nav class="context-tabs" aria-labelledby="context-heading">'+tabs.map(([id,title])=>
      '<button type="button" class="context-tab" data-do="tab" data-value="'+id+'" aria-pressed="'+(current===id)+'">'+icon(id)+escape(title)+'</button>').join('')+
    '</nav>'+modules[current]()+'</div>';
  const launcher = '<button class="launcher '+(launcherSeen?'':'invite')+'" type="button" data-do="open-chat" aria-label="Открыть чат поддержки"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M5 4h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2h-8l-6 4v-4H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z"/><path d="M7 9h10M7 13h7"/></svg></button>';
  return {title:'ИИ ассистент поддержки',html:'<div class="'+(settings?'buyer-layout':'')+'">'+settings+store+'</div>'+(chatOpen?buyerChat(d):launcher)};
}
function handoffLabel(d) {
  if (d?.status==='resolved') return 'Чат закрыт';
  if (d?.status==='queued') return 'В очереди к менеджеру';
  if (d?.status==='assigned') return list(d.messages).some(m=>messageRole(m)===d.assignment || m.joined===d.assignment) ? 'Менеджер подключился' : 'Чат передан менеджеру';
  return '';
}
function buyerChat(d) {
  const viewedOffer = (tab ?? 'catalog')==='catalog' ? clientContext().viewed : undefined;
  const productQuestion = ['offer','group'].includes(referenceDraft?.type);
  const greeting = productQuestion ? 'Здравствуйте! Что хотите узнать об этом товаре?' : viewedOffer?.group_name ? 'Какая лицензия нужна для «'+viewedOffer.group_name+'»?' :
    'Здравствуйте! Ищете конкретную программу или помочь с выбором?';
  const questions={
    group:['Подобрать лицензию'],
    order:['Какой статус заказа?','Ключ уже готов?'],
    license:['Лицензия ещё действует?','Где посмотреть лицензию?']
  };
  const kind=referenceDraft?.type ?? (viewedOffer && (viewedOffer.group_name?'group':'offer'));
  // The live assistant starts from free-form text. The button scenario stays
  // available for the customer's decision: open the stand with ?scripted=1.
  const scriptedStarters=new URLSearchParams(location.search).has('scripted');
  const starters=questions[kind]?questions[kind].map(q=>button(q,'context-question',q)).join(''):
    referenceDraft?'':viewedOffer?button('Задать вопрос о товаре','ask-product',viewedOffer.id):
    scriptedStarters?button('Ищу программу','starter','search')+button('Помогите с выбором','starter','choose'):'';
  const menu='<div class="chat-options" id="chat-options" '+(chatPane==='options'?'':'hidden')+'>'+
    button('Новый чат','new-dialog','')+button(soundMuted?'Включить звук':'Выключить звук','sound','')+
    (d && !d.outcome && d.status==='open'?button('Завершить консультацию','end-chat',d.id):'')+
    (session.role==='jury' && !d?.outcome && (!d || d.status==='open')?button('Ночная передача','night-transfer',d?.id ?? ''):'')+
    ((!d || d.status==='open')?button('Позвать менеджера','call-manager',d?.id ?? ''):'')+
    button('Мои обращения','chat-history','')+'</div>';
  const history=chatPane==='history'?'<div class="chat-history"><div class="history-heading"><h3>Мои обращения</h3>'+button('К переписке','chat-thread','')+'</div>'+chatHistory()+'</div>':'';
  const thread = d?conversation(d):(!productQuestion && (referenceDraft || kind==='offer'))?'':'<p class="bubble">'+escape(displayText(greeting))+'</p>';
  const last=d?.messages.at(-1);
  let choices=list(last?.choices);
  if (!choices.length && last?.offers?.some(o=>o.group_recommendation)) {
    choices=last.offers.length>1?['Чем отличаются варианты?']:[];
  }
  if (d?.offer_email) choices=[...choices,'Отправить на почту'];
  const status = handoffLabel(d);
  return '<section class="widget" aria-label="Помощник Softline"><div class="widget-head"><img src="/sofl.png" alt=""><h2>Помощник Softline</h2>'+
    iconButton('more','Опции чата','chat-options')+iconButton('close','Закрыть чат','close-chat')+'</div>'+menu+history+
    '<div class="thread" '+(history?'hidden':'')+'>'+thread+'</div><div class="chat-foot" '+(history?'hidden':'')+'><p class="chat-error" role="alert"></p>'+
    (status?'<p class="handoff" role="status">'+escape(status)+'</p>':'')+
    (d?.outcome?button('Итоги диалога','dialog-result',d.id):
      d && (chatPane==='confirm' || ['inform','lookup','shortlist'].includes(d.decision?.next_action) && chatPane!=='continue')?
      '<div>Мы решили ваш вопрос? '+button('Да','confirm-resolved',d.id)+button('Нет','continue-chat','')+'</div>':'')+
    (role==='guest' && d?.decision?.next_action==='login'?
      (session.role==='jury'?button('Переключиться на клиента','role','buyer'):button('Войти в кабинет','customer-login','')):'')+
    (!checkoutOffer && !referenceDraft && d?.status!=='resolved' && choices.length ? '<div class="quick-actions">'+
      choices.map(value=>button(value,'chat-choice',value)).join('')+'</div>' : '')+
    (d?.status==='resolved'?button('Начать новый чат','new-dialog',''):
      (checkoutOffer?checkoutForm(checkoutOffer):
        ((!d || referenceDraft) && starters?'<div class="quick-actions">'+starters+'</div>':'')+composer('message',buyerDraft)))+
    '</div></section>';
}
function checkoutForm(offer) {
  const minimum=quantityOf(offer), maximum=offer.max_quantity || 100000;
  const saved=clientContext().cart.get(String(offer.id))?.quantity;
  const quantity=saved ?? offer.suggested_quantity ?? (minimum===maximum?minimum:undefined);
  const known=Number.isInteger(quantity) && quantity>=minimum && quantity<=maximum;
  const approved=activeDialog()?.approved_discount;
  return form('document','<div class="checkout-heading"><strong>'+escape(displayText(offer.name))+'</strong>'+
    iconButton('close','Закрыть оформление','cancel-checkout')+'</div>'+
    hidden('offer_id',offer.id)+hidden('expected_price',offer.price)+
    (known?hidden('quantity',quantity)+'<p>'+num(quantity)+' шт.</p>':
      field('Количество','quantity','','type="number" min="'+minimum+'" max="'+maximum+'" required')+quantityTerms(offer))+
    (String(approved?.offer_id)===String(offer.id)?'<p>Согласованная скидка '+num(approved.rate)+'%</p>':'')+
    '<div class="row"><button class="primary" name="kind" value="invoice">Получить счёт</button><button name="kind" value="proposal">Получить КП</button></div>');
}
function composer(action,draft,id='') {
  const quote=quoteDraft?'<div class="composer-note"><blockquote><b>'+escape(label(quoteDraft.role))+'</b><div>'+escape(displayText(quoteDraft.preview))+'</div></blockquote>'+
    iconButton('close','Убрать цитату','clear-quote')+'</div>':'';
  const file=fileDraft?'<div class="composer-note"><span>'+escape(fileDraft.name)+'</span>'+iconButton('close','Убрать файл','clear-file')+'</div>':'';
  const reference=action==='message' && referenceDraft?'<div class="composer-note"><span class="message-reference">'+icon('attach')+
    escape(displayText(referenceDraft.label))+'</span>'+iconButton('close','Убрать позицию','clear-reference')+'</div>':'';
  return form(action,(id?hidden('dialog_id',id):'')+quote+file+reference+
    '<div class="composer-line"><input type="file" hidden>'+iconButton('attach','Прикрепить файл','attach')+
    '<textarea name="text" rows="2" maxlength="4000" aria-label="Сообщение">'+escape(draft)+'</textarea>'+
    iconButton('emoji','Выбрать эмодзи','emoji-picker')+iconButton('send','Отправить сообщение','')+'</div>'+
    '<div class="emoji-picker" hidden>'+['🙂','👍','👋','🙏','✅','❓'].map(emoji=>button(emoji,'emoji',emoji)).join('')+'</div>');
}
function chatHistory() {
  const groups=new Map();
  const seen=new Set();
  for (const d of list(state.dialogs).slice().reverse()) {
    if (seen.has(d.id) || !d.messages?.length) continue;
    seen.add(d.id);
    const title=list(d.messages).find(m=>messageRole(m)==='buyer' && !['Ищу программу','Помогите с выбором'].includes(m.text))?.text || selectDialogLabel(d);
    const key=title.trim().toLocaleLowerCase('ru');
    if (!groups.has(key)) groups.set(key,[]);
    groups.get(key).push({d,title});
  }
  return rows([...groups.values()], group=>{
    const entry=({d,title})=>{
      const date=new Date(d.created_at).toLocaleString('ru-RU',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'});
      return button((group.length===1?title.slice(0,100)+' · ':'')+date+' · '+label(d.status)+' · '+d.id,'open-dialog',d.id,'class="order"');
    };
    if (group.length===1) return entry(group[0]);
    return '<details><summary>'+escape(group[0].title.slice(0,100))+' ('+group.length+')</summary>'+group.map(entry).join('')+'</details>';
  },'Чатов пока нет.');
}
const catalogOffers = () => catalogSearch?.offers ?? list(state.catalog ?? state.offers).slice(0,8);
function catalogPage() {
  const context = clientContext();
  const page=catalogSearch?.page ?? 1;
  const hasMore=catalogSearch?.hasMore ?? state.catalog_has_more;
  const pagination=(hasMore || page>1)?'<nav class="catalog-pages" aria-label="Страницы каталога">'+
    button('Назад','catalog-page',page-1,page===1?'disabled':'')+'<span>Страница '+num(page)+'</span>'+
    button('Дальше','catalog-page',page+1,hasMore?'':'disabled')+'</nav>':'';
  return '<section aria-label="Каталог программ">'+form('search','<div class="catalog-search"><input type="search" name="query" aria-label="Поиск по каталогу" value="'+
    attr(catalogSearch?.query ?? '')+'" maxlength="220">'+submit('Найти')+'</div>')+
    '<div class="catalog-grid">'+
    rows(catalogOffers(), offer => {
      const grouped=Boolean(offer.group_name);
      const price=grouped ? (offer.group_price_from>0 ? 'от '+escape(num(offer.group_price_from))+' '+escape(offer.group_price_currency) : '')
                          : offerPrice(offer,false);
      const id = String(offer.id);
      const url = grouped?'':safeUrl(offer.url);
      const viewed=grouped?context.viewed?.group_name && context.viewed.group_id===offer.group_id:String(context.viewed?.id)===id;
      const name=displayText(offer.group_name || offer.name);
      const brand=displayText(offer.vendor || name.split(/\s+/)[0]);
      const color=Array.from(String(offer.group_id || id)).reduce((sum,char)=>sum+char.charCodeAt(0),0)%4;
      const description=grouped?'':offerDetails(offer).description;
      return '<article class="product" data-viewed="'+Boolean(viewed)+'"><button class="product-select" type="button" data-do="select-product" data-value="'+attr(id)+
        '" aria-label="'+attr('Выбрать '+name)+'" aria-pressed="'+Boolean(viewed)+'"></button>'+
        '<div class="product-art product-art-'+color+'" aria-hidden="true"><div class="product-cover">'+escape(brand)+'</div></div>'+
        iconButton('question','Спросить о товаре','ask-product',id,'product-question')+
        '<div class="product-copy">'+(offer.category?'<p class="product-category">'+escape(offer.category)+'</p>':'')+'<h3>'+escape(name)+'</h3>'+
        (description && description!==offer.name?'<p>'+escape(description)+'</p>':'')+(grouped?'':quantityTerms(offer))+'</div>'+
        '<div class="product-bottom">'+(price?'<strong>'+price+'</strong>':'')+'<div class="product-actions">'+
        (grouped?button('Подобрать лицензию','choose-group',id,'class="primary"'):cartControls(offer, context.cart.get(id)))+
        (url?'<a class="chat-icon" href="'+attr(url)+'" target="_blank" rel="noreferrer" aria-label="Карточка на сайте" title="Карточка на сайте">'+icon('external')+'</a>':'')+'</div></div></article>';
    },catalogSearch?.query?'Товары не найдены. Уточните запрос.':'Товаров пока нет.')+'</div>'+pagination+'</section>';
}
function cartControls(offer, line) {
  if (!(offer.price>0)) return button('Запросить цену','price-request',offer.id);
  if (!line) return iconButton('cart','Добавить в корзину','cart-add',offer.id,'primary');
  const plus=line.quantity<(offer.max_quantity || 100000);
  return '<div class="quantity">'+iconButton('minus','Уменьшить количество','cart-minus',offer.id)+
    '<span aria-live="polite">'+num(line.quantity)+'</span>'+
    (plus?iconButton('plus','Увеличить количество','cart-plus',offer.id):'<span aria-label="Максимальное количество">'+icon('plus')+'</span>')+'</div>';
}
function cartPage() {
  const cart = [...clientContext().cart.values()];
  const totals=new Map();
  for (const {offer,quantity} of cart) {
    totals.set(offer.currency,(totals.get(offer.currency) ?? 0)+Math.round(offer.price*100)*quantity);
  }
  return '<section class="panel" aria-label="Корзина">'+rows(cart, ({offer,quantity}) => item(displayText(offer.name),
    '<p>'+offerPrice(offer,false)+' за шт. · Сумма '+num(Math.round(offer.price*100)*quantity/100)+' '+escape(offer.currency)+'</p>'+
    '<div class="row">'+cartControls(offer,{offer,quantity})+button('Удалить','cart-remove',offer.id)+
    iconButton('question','Спросить о товаре','ask-cart-product',offer.id)+'</div>'),
  'В корзине пока ничего нет. Добавьте товар из каталога.')+
  (totals.size?'<p role="status"><strong>Итого '+[...totals].map(([currency,total])=>num(total/100)+' '+escape(currency)).join(' + ')+'</strong></p>':'')+'</section>';
}
function documentDescription(d) {
  const name=d.name ?? d.product_name ?? d.filename ?? '';
  if (d.quantity==null) return name;
  if (/количество почтовых ящиков\s*$/i.test(name)) return name+': '+num(d.quantity);
  return name+'. Количество: '+num(d.quantity);
}
function paymentsPage() {
  const documents=list(state.documents);
  const payments=list(state.orders);
  const settled=payments.map(o=>item(o.name ?? o.id,'<div class="row"><span>'+escape(o.id)+' · '+escape(o.status)+'</span>'+
    iconButton('question','Спросить о заказе','ask-order',o.id)+'</div>')).join('');
  const files=documents.map(d=>item((label(d.kind ?? d.type)||'Документ')+' '+(d.number ?? d.id),
    '<p>'+escape(documentDescription(d))+'</p><div class="row"><span>'+escape(num(d.total))+' '+escape(d.currency)+'</span>'+
    iconButton('download',label(d.kind ?? d.type)==='КП'?'Скачать КП':'Скачать счёт','download',d.id)+
    iconButton('question','Спросить о документе','ask-document',d.id)+'</div>')).join('');
  return '<section class="panel" aria-label="Счета и платежи">'+(files+settled || '<p class="muted">Счетов, КП и платежей пока нет.</p>')+'</section>';
}
function keysPage() {
  return '<section class="panel" aria-label="Дистрибутивы и ключи">'+rows(list(state.licenses),license=>{
    const info=license.status==='expired'?'Срок лицензии истёк':'<code>'+escape(license.key)+'</code>'+
      iconButton('copy','Скопировать ключ','copy-license',license.id)+iconButton('download','Скачать учебный файл лицензии','download-license',license.id);
    return item(displayText(license.name),'<p>'+escape(license.id)+'</p><div class="row">'+info+
      iconButton('question','Спросить о лицензии','ask-license',license.id)+'</div>');
  },'Ключей пока нет.')+'</section>';
}

function staffPage() {
  const developer=role==='developer';
  const tabs = developer || training ? [
    ['reviews','Ответы на проверку'],['queue','Разбор чатов'],['samples','Разметка'],['templates','Шаблоны'],['journal','Журнал'],
    ...(developer?[['stand','Стенд']]:[])
  ] : [['queue','Чаты']];
  const current = tab ?? tabs[0][0];
  const modules = {queue:queuePage,samples:samplesPage,templates:templatesPage,reviews:reviewsPage,journal:journalPage,stand:()=>panel('Параметры стенда',standPage())};
  const mode=developer?'':choices([['work','Чаты'],['training','Обучение']],'workspace',training?'training':'work');
  return {title:developer?'Управление поддержкой':label(role),html:mode+(tabs.length>1?choices(tabs,'tab',current):'')+'<div class="form">'+modules[current]()+'</div>'};
}
const selectDialogLabel = d => list(d.messages).find(m=>messageRole(m)==='buyer')?.text?.slice(0,100) ?? ('Обращение '+d.id);
let telegramOpen=false;
const managerKey=()=> 'softline/'+session.role+'/'+role+'/';
const alertsEnabled=()=>window.Notification?.permission==='granted' && localStorage.getItem(managerKey()+'alerts')==='on';
const revision=d=>list(d.messages).filter(m=>messageRole(m)==='buyer').at(-1)?.id || 'assigned';
const readKey=d=>managerKey()+'read/'+d.id;
function managerList(dialogs) {
  const groups=[];
  for (const resolved of [false,true]) {
    const entries=dialogs.filter(d=>(d.status==='resolved')===resolved).reverse();
    const title=resolved?'Решённые':'Нерешённые';
    groups.push('<h3>'+title+' <small>'+entries.length+'</small></h3>'+entries.map(d=>{
      const unread=localStorage.getItem(readKey(d))!==revision(d);
      return '<button class="order manager-ticket '+(resolved?'closed':'pending')+'" data-do="dialog" data-value="'+attr(d.id)+'" aria-pressed="'+(d.id===dialogId)+'"><b>'+escape(selectDialogLabel(d))+'</b><small>'+escape(d.id)+(unread?' <strong>Непрочитано</strong>':'')+'</small></button>';
    }).join(''));
  }
  const waiting=list(state.queue);
  if (waiting.length) groups.splice(1,0,'<h3>Общая очередь <small>'+waiting.length+'</small></h3>'+waiting.map(x=>item(x.reason || 'Запрос покупателя','<p>'+escape(x.id)+'</p>'+button('Взять обращение','claim',x.id))).join(''));
  return '<div class="manager-list">'+groups.join('')+'</div>';
}
function queuePage() {
  const dialogs = list(state.dialogs);
  const d = activeDialog();
  const manager = state.managers?.[role] ?? list(state.managers).find(m=>(m.id ?? m.role)===role);
  const learning=training || role==='developer';
  if (!learning && d && !document.hidden) localStorage.setItem(readKey(d),revision(d));
  let queue = learning ? rows(dialogs,x=>'<button type="button" class="order" data-do="dialog" data-value="'+attr(x.id)+'" aria-pressed="'+(d?.id===x.id)+'"><b>'+escape(selectDialogLabel(x))+'</b><small>'+escape(label(x.status))+' · '+escape(label(x.assignment ?? 'Без назначения'))+'</small></button>','В очереди пока нет обращений.') : managerList(dialogs);
  if (!learning) {
    const online=manager?.available;
    const enabled=alertsEnabled();
    queue+='<div class="manager-tools"><span>'+(online?'На линии':'Не на линии')+'</span>'+
      iconButton('send','Настройки Telegram','telegram-settings')+
      iconButton('bell',enabled?'Выключить уведомления':'Включить уведомления','browser-alerts','',enabled?'enabled':'')+
      iconButton('power',online?'Уйти с линии':'Выйти на линию','availability',!online,online?'enabled':'')+'</div>'+
      (telegramOpen ? telegramSettings() : '');
  }
  let detail = conversation(d,true);
  if (d) {
    detail = '<p class="muted">'+escape(label(d.status))+' · '+escape(label(d.assignment ?? 'Без назначения'))+'</p>'+
      (d.handoff_summary?'<details open><summary>Кратко об обращении</summary><p>'+escape(d.handoff_summary).replaceAll('\n','<br>')+'</p></details>':'')+
      (d.outcome?button('Итоги диалога','dialog-result',d.id):'')+
      (learning && d.route?'<details><summary>Маршрутизация</summary>'+routeDetails(d.route)+'</details>':'')+
      (!learning?'<details><summary>Данные заказа</summary>'+rows(list(d.linked_orders),o=>item(o.name,'<p>'+escape(o.id)+' · '+escape(o.status)+' · '+escape(o.license_status)+'</p>'),'В чате пока нет связанного заказа.')+'</details>':'')+'<div class="staff-thread">'+detail+'</div>';
    if (!learning && d.status!=='resolved') {
      detail += '<div class="quick-actions">'+form('copilot',hidden('dialog_id',d.id)+submit('Подготовить ответ'))+'</div>'+
        composer('reply',staffDraft,d.id);
    }
    if (!learning && d.status!=='resolved') {
      detail += '<details><summary>Передача обращения</summary>'+form('reassign',hidden('dialog_id',d.id)+'<label>Менеджер<br>'+choose('manager_id',Object.keys(state.managers ?? {}).filter(id=>id!==role).map(id=>[id,label(id)]))+'</label>'+submit('Передать'))+'</details>'+button('Закрыть обращение','resolve',d.id);
    }
    if (learning) detail += '<details><summary>Проверить подсказку</summary>'+
      form('copilot',hidden('dialog_id',d.id)+'<label>Как подготовить ответ'+choose('mode',modes,result?.model?.requested_mode ?? 'rules')+'</label>'+submit('Подготовить черновик'))+
      (result?.text?'<p>'+escape(result.text)+'</p>':'')+'</details>'+
      '<details><summary>Разметка чата</summary>'+annotationForm('moderate','dialog_id',d)+'</details>';
  }
  return '<div class="grid">'+panel(learning?'Очередь чатов':'Переданные диалоги',queue)+panel('Переписка',detail)+'</div>';
}
function telegramSettings() {
  const {available, expires} = state.telegram || {};
  const until = expires && new Intl.DateTimeFormat('ru-RU',{dateStyle:'short',timeStyle:'short',timeZone:'Europe/Moscow'})
    .format(new Date(expires*1000));
  const body = expires
    ? '<p role="status">Подключено до '+escape(until)+' МСК.</p>'+button('Отключить','telegram-disconnect','')
    : available ? '<p>Отправьте /start в личном чате с <a href="https://t.me/jtd_test_bot" target="_blank" rel="noreferrer">@jtd_test_bot</a>. Код действует 5 минут.</p>'+
        form('telegram_connect',field('Код из Telegram','code','','inputmode="numeric" pattern="[0-9]{10}" maxlength="10" required autocomplete="off"')+submit('Подключить'))
      : '<p>Бот сейчас недоступен. Обновите обращения позже.</p>';
  return '<section class="telegram-settings"><h3>Уведомления в Telegram</h3>'+body+'</section>';
}
function routeDetails(route) {
  if (typeof route==='string') return '<p>'+escape(route)+'</p>';
  const keys = {reason:'Причина',rule:'Правило',intent:'Намерение',queue:'Очередь',skill:'Компетенция',expertise:'Компетенция',assigned_to:'Назначен',assignment:'Назначен',status:'Статус',category:'Категория'};
  return '<dl class="summary">'+Object.entries(route).filter(([key,value])=>keys[key] && typeof value!=='object')
    .map(([key,value])=>'<div><dt>'+keys[key]+'</dt><dd>'+escape(label(value))+'</dd></div>').join('')+'</dl>'+
    Object.entries(route.candidates ?? {}).map(([id,m])=>'<p>'+escape(label(id))+' · '+(m.available?'На линии':'Не на линии')+' · Обращений '+num(m.load)+' · '+escape(list(m.expertise).join(', '))+'</p>').join('');
}
const check = (title,key,value) => '<label class="inline"><input type="checkbox" name="'+key+'" '+(value?'checked':'')+'>'+title+'</label>';
function annotationForm(action,key,s) {
  return form(action,hidden(key,s.id)+field('Метка','label',s.label ?? '', 'maxlength="120"')+
    (action==='sample_classify'?'<div class="row">'+field('Тип клиента','buyer_type',s.facets?.buyer_type,'maxlength="120"')+
      field('Срок лицензии','license_term',s.facets?.license_term,'maxlength="120"')+
      field('Срок поставки','delivery_term',s.facets?.delivery_term,'maxlength="120"')+'</div>':'')+area('Комментарий','note',s.note ?? '', 'maxlength="1500"')+
    '<div class="row">'+check('Ручная проверка','manual_review',s.flags?.manual_review)+check('В обучающую выборку','collect_sample',s.flags?.collect_sample)+'</div>'+submit('Сохранить'));
}
function samplesPage() {
  const all = list(state.samples);
  const filtered = all.filter(s=>[s.text,s.label,s.source_label,s.note].join(' ').toLowerCase().includes(sampleQuery.toLowerCase()));
  const count = Math.max(1,Math.ceil(filtered.length/12));
  samplePage = Math.min(samplePage,count-1);
  return panel('Классификация обращений',form('filter',field('Текст или метка','query',sampleQuery)+submit('Найти'))+
    '<p class="muted">Совпадений '+num(filtered.length)+' из '+num(all.length)+'</p>'+
    rows(filtered.slice(samplePage*12,samplePage*12+12),s=>'<details class="item" '+(result?.sample?.id===s.id?'open':'')+'><summary>'+escape(s.text?.slice(0,150) ?? s.id)+'</summary><p>'+escape(s.text)+'</p><p class="muted">'+escape(s.source_label)+'</p>'+annotationForm('sample_classify','sample_id',s)+'</details>','Совпадений нет.')+
    '<div class="row">'+button('Назад','sample-page',samplePage-1,samplePage===0?'disabled':'')+
    '<span>Страница '+(samplePage+1)+' из '+count+'</span>'+button('Вперёд','sample-page',samplePage+1,samplePage>=count-1?'disabled':'')+'</div>');
}
function templateForm(t={}) {
  return form('template_draft',(t.id?hidden('template_id',t.id):'')+field('Название','name',t.name,'required maxlength="160"')+
    '<label>Тип<br>'+choose('kind',[['answer','Ответ'],['quote','КП'],['invoice','Счёт']],t.kind)+'</label>'+
    area('Текст шаблона','text',t.text,'required maxlength="4000"')+'<small>Переменные: {question}, {product_name}, {price}, {currency}.</small>'+submit(t.id?'Сохранить черновик':'Создать черновик'));
}
function templatesPage() {
  const developer = role==='developer';
  const preview = result?.template?.emulation;
  return panel('Шаблоны',preview? '<div class="note">'+escape(preview.text)+list(preview.offers).map(o=>chatOffer(o,true)).join('')+'</div>':'')
    +'<div class="form"><details><summary>Новый шаблон</summary>'+templateForm()+'</details>'+
    rows(list(state.templates),t=>'<details class="item" '+(result?.template?.id===t.id?'open':'')+'><summary>'+escape(t.name ?? label(t.kind))+' · '+escape(label(t.status))+'</summary>'+
      '<pre>'+escape(t.text)+'</pre>'+
      (t.status==='draft'?'<details><summary>Редактировать</summary>'+templateForm(t)+'</details>':'<details><summary>Создать версию</summary>'+templateForm(t)+'</details>')+
      form('template_emulate',hidden('template_id',t.id)+field('Тестовый запрос','text','Подберите товар','maxlength="500"')+submit('Проверить шаблон'))+
      ('<div class="row">'+(t.status==='draft'?(developer?button('Опубликовать','template_publish',t.id,t.emulation?'':'disabled'):'')+button('Удалить черновик','template_delete',t.id):'')+
        (developer&&t.status==='retired'?button('Восстановить версию','template_rollback',t.id):'')+'</div>')+'</details>','Шаблонов пока нет.')+'</div>';
}
function reviewSource(value) {
  if (!value) return '';
  const url=safeUrl(value);
  return url ? '<p><a href="'+attr(url)+'" target="_blank" rel="noreferrer">Источник ответа</a></p>' :
    '<p class="muted">Источник '+escape(value==='xml'?'Каталог':value==='synthetic'?'Учебный каталог':value)+'</p>';
}
function replaySteps(run) {
  const actions = {clarify:'Уточнение',shortlist:'Подборка',inform:'Справка',handoff:'Передача менеджеру',confirm_offer:'Проверка позиции'};
  const needs = {intent:'тема обращения',discount_acceptance:'согласие на 5%',product:'товар',seats:'число мест',article:'артикул'};
  const steps = list(run.steps).map((step,i)=>'<details><summary>'+(step.separate?'Отдельная проверка ':'Шаг ')+(i+1)+'. '+escape(actions[step.action] || label(step.action || 'Ответ'))+'</summary>'+
    '<blockquote>'+escape(step.fragment)+'</blockquote>'+
    (step.intent?'<p>'+escape(({discount:'Подходит ли покупателю скидка?',unclear:'Понятна ли тема обращения?',selection:'Какая позиция подходит?',security:'Какая защита нужна?'})[step.intent] || 'Как обработать эту просьбу?')+'</p>':'')+
    (step.evidence?.length?'<p>Основание: '+step.evidence.map(escape).join(', ')+'</p>':'')+
    (step.missing?.length?'<p>Нужно уточнить: '+step.missing.map(k=>escape(needs[k] || label(k))).join(', ')+'</p>':'')+
    '<p>'+escape(step.text)+'</p>'+reviewSource(step.source)+(step.template?'<p>Шаблон '+escape(step.template)+'</p>':'')+'</details>').join('');
  const v=run.variants || {};
  return steps+(v.scenario?'<details><summary>Варианты исходного ответа</summary><p>'+escape(run.original_template?'Шаблон '+run.original_template:'Сценарный ответ')+'</p><blockquote>'+escape(v.scenario)+'</blockquote>'+
    (v.draft?'<p>Черновик модели</p><blockquote>'+escape(v.draft)+'</blockquote>':'')+
    (v.reason?'<p>'+escape(v.reason)+'</p>':'')+'<p>'+escape(run.model_failed?'Сбой модели. Использован сценарный ответ.':run.model_published?'Использовано уточнение модели.':'Использован сценарный ответ.')+'</p></details>':'');
}
function reviewsPage() {
  const replay=result?.replay;
  const latest=result?.review;
  return panel('Ответы на проверку',rows(list(state.review_queue),r=>{
    const comparison=replay?.message_id===r.message_id ? replay : undefined;
    const selected=comparison || latest?.message_id===r.message_id;
    const ids=hidden('dialog_id',r.dialog_id)+hidden('message_id',r.message_id)+hidden('version',r.version);
    const rating=latest?.message_id===r.message_id ? latest.rating : r.previous_rating;
    const current=typeof rating==='object' ? rating?.rating : rating;
    return '<details class="item" '+(selected?'open':'')+'><summary>'+escape(r.question || 'Обращение '+r.dialog_id)+
      (current?' · Оценка '+escape(current==='-'?'−':current):'')+'</summary>'+
      '<div class="review-answers"><div><h3>Исходный ответ</h3><p>'+escape(r.answer)+'</p>'+reviewSource(r.source)+'</div>'+
      (comparison?'<div><h3>Повтор на текущем сценарии</h3><p>'+escape(comparison.answer)+'</p>'+reviewSource(comparison.source)+'</div>':'')+'</div>'+
      (comparison?replaySteps(comparison):'')+
      '<p class="muted">Версия исходного ответа '+escape(r.version)+'</p>'+
      form('review_replay',ids+submit('Повторить сценарий'))+
      form('review',ids+area('Закрытый комментарий','note','','maxlength="1500"')+
        '<div class="row"><button class="primary" name="rating" value="+">+ Полезно</button><button name="rating" value="-">− Нужна правка</button></div>')+'</details>';
  },'Ответов для проверки пока нет.'));
}
function journalPage() {
  const reviews = list(state.reviews);
  const journal = list(state.journal ?? state.audit);
  const actions = {message:'Сообщение',reply:'Ответ менеджера',claim:'Взятие обращения',reassign:'Передача обращения',resolve:'Закрытие обращения',review:'Оценка ответа',review_replay:'Повтор сценария',availability:'Доступность менеджера',moderate:'Разметка обращения',sample_classify:'Разметка записи',template_draft:'Правка шаблона',template_emulate:'Проверка шаблона',template_publish:'Публикация шаблона',template_delete:'Удаление черновика',template_rollback:'Восстановление версии',feed_import:'Загрузка каталога',buyer_select:'Смена профиля клиента',invoice:'Счёт',proposal:'КП',copilot:'Подсказка'};
  return panel('Журнал',stats([['Оценок',num(reviews.length)]])+rows(journal.filter(e=>e.action!=='feed_import'),e=>item(e.title ?? actions[e.action] ?? e.action ?? e.event,'<p>'+escape(e.at ?? e.created_at)+' '+escape(label(e.role ?? e.actor))+'</p>'),'Записей пока нет.')+
    '<h3>Оценки ответов</h3>'+rows(reviews,r=>item((r.rating ?? r.label ?? 'Разметка')+' · '+(r.dialog_id ?? r.sample_id ?? ''),
      '<p>'+escape(r.note)+' '+escape(r.at ?? r.created_at)+'</p>'),'Оценок пока нет.'));
}
function standPage() {
  const providerNames={jev:'Jev',cloud:'Облачная модель',nimble:'Nimble',local_llm:'Локальная модель'};
  const usage=Object.entries(state.usage ?? {}).filter(([,value])=>value.calls>0);
  const feed=state.feed ?? {};
  const changes=rows(list(feed.changes),c=>item(c.article || c.id,'<p>'+escape(num(c.before))+' → '+
    escape(num(c.price))+' '+escape(c.currency ?? '')+'</p>'),'Изменений цен нет.');
  return '<h3>Версия учебного каталога</h3><div class="quick-actions">'+
    [1,2].map(day=>button('Версия '+day,'catalog-day',day,day===feed.day?'disabled':'')).join('')+
    '</div><h3>Изменившиеся артикулы</h3>'+changes+'<h3>Вызовы моделей</h3>'+rows(usage,([key,value])=>item(providerNames[key] ?? key,
    '<p>Запросов '+num(value.calls)+' · С учётом токенов '+num(value.metered_calls)+'</p>'),'Запросов к моделям пока нет.');
}
function showError(error) {
  errorText = error.message;
  if (!session) renderLogin();
  const status = document.querySelector('#status');
  const local=app.querySelector('.chat-error');
  if (local) { status.textContent=''; local.textContent=errorText; }
  else if (status) { status.textContent=errorText; status.focus(); }
}
async function run(task, trigger, restoreFocus) {
  if (busy) return;
  busy = true;
  errorText = '';
  const status = document.querySelector('#status');
  const thread=app.querySelector('.thread, .staff-thread');
  const scroll=thread?.scrollTop;
  if (status) status.textContent = 'Выполняется…';
  if (trigger) trigger.disabled=true;
  try {
    await task();
    render();
    const currentThread=app.querySelector('.thread, .staff-thread');
    if (currentThread && scroll!==undefined) currentThread.scrollTop=scroll;
    restoreFocus?.();
  } catch (error) { showError(error); }
  finally {
    if (trigger) trigger.disabled=false;
    busy = false;
  }
}
function payloadOf(formElement, submitter) {
  const payload = Object.fromEntries(new FormData(formElement));
  for (const name of ['manual_review','collect_sample']) {
    if (formElement.elements[name]) payload[name] = formElement.elements[name].checked;
  }
  for (const name of ['quantity','discount','expected_price']) {
    if (name in payload) payload[name] = Number(payload[name]);
  }
  if (submitter?.name) payload[submitter.name] = submitter.value;
  return payload;
}
function readFile(file) {
  return new Promise((resolve,reject)=>{
    const reader=new FileReader();
    reader.onload=()=>resolve(reader.result.split(',',2)[1]);
    reader.onerror=()=>reject(Error('Файл не прочитан. Выберите его снова.'));
    reader.readAsDataURL(file);
  });
}
app.addEventListener('submit', event => {
  event.preventDefault();
  const formElement = event.target;
  const action = formElement.dataset.action;
  if (!action) return;
  const p = payloadOf(formElement,event.submitter);
  const message=action==='message' || action==='reply';
  if (message && !p.text.trim() && !fileDraft) return;
  run(async () => {
    if (action==='login') { await signIn(p.token.trim()); return; }
    if (action==='telegram_connect') {
      state.telegram = await request('/api/action',{role,action,payload:{code:p.code}});
      return;
    }
    if (action==='filter') { sampleQuery=p.query.trim(); samplePage=0; return; }
    if (action==='message') {
      p.mode=buyerMode;
      if (dialogId) p.dialog_id=dialogId;
      const context = clientContext();
      if (referenceDraft) {
        p.reference={type:referenceDraft.type,id:referenceDraft.id};
      }
      if ((!referenceDraft && (tab==='cart' || /корзин/i.test(p.text))) ||
          (referenceDraft?.fromCart && context.cart.has(referenceDraft.id))) {
        p.page_context={type:'basket',items:[...context.cart.values()].map(({offer,quantity})=>({offer_id:String(offer.id),quantity}))};
        if (referenceDraft?.fromCart) p.page_context.selected_offer_id=referenceDraft.id;
      } else if (!referenceDraft && (!tab || tab==='catalog') && context.viewed) {
        if (context.viewed.group_name) p.viewed_group_id=String(context.viewed.group_id);
        else p.viewed_offer_id=String(context.viewed.id);
      }
    }
    if (message && quoteDraft) {
      p.reply_to=quoteDraft.id;
      p.quote=quoteDraft.excerpt;
    }
    if (message && fileDraft) p.attachment={name:fileDraft.name,content:await readFile(fileDraft)};
    if (['message','reply','document'].includes(action)) {
      const fingerprint=JSON.stringify(p);
      const previous=requestIds.get(formElement);
      const id=previous?.fingerprint===fingerprint ? previous.id : crypto.randomUUID();
      requestIds.set(formElement,{fingerprint,id});
      p.request_id=id;
    }
    if (action==='document') {
      const kind = p.kind;
      delete p.kind;
      if (dialogId) p.dialog_id=dialogId;
      await act(kind,p);
      checkoutOffer=undefined;
      return;
    }
    if (action==='search') { await loadCatalog(p.query.trim(),1); return; }
    await act(action,p);
    if (action==='copilot' && !training) staffDraft=result.text ?? '';
    if (message) {
      clearComposer();
      if (action==='message' && messageRole(list(result.dialog?.messages).at(-1) ?? {})==='assistant') notifyMessage();
    }
  }, event.submitter, ()=>{
    if (action==='telegram_connect') app.querySelector('[data-do=telegram-settings]')?.focus();
    if (action==='search') app.querySelector('.catalog-search input')?.focus();
    app.querySelector('[data-action="'+(action==='document'?'message':action)+'"] textarea')?.focus();
    const thread=app.querySelector(action==='reply'?'.staff-thread':'.thread');
    if (thread) thread.scrollTop=thread.scrollHeight;
  });
});
const catalogOffer = id => catalogOffers().find(offer=>String(offer.id)===id);
let selectedQuote='';
function selectedExcerpt(control) {
  const selection=getSelection();
  const message=control.closest('.bubble')?.querySelector('.message-text');
  return message && selection && message.contains(selection.anchorNode) && message.contains(selection.focusNode)
    ? Array.from(selection.toString().trim()).slice(0,240).join('') : '';
}
app.addEventListener('pointerdown',event=>{
  const reply=event.target.closest('[data-do="quote"]');
  selectedQuote=reply?selectedExcerpt(reply):'';
});
document.addEventListener('click', event => {
  if (chatOpen && storefront() && !event.target.closest('.widget, .buyer-layout, .shop, .dialog-result')) {
    chatOpen=false;
    chatPane='';
    const widget=app.querySelector('.widget');
    if (widget) {
      const replacement=document.createElement('template');
      replacement.innerHTML=buyerPage().html;
      widget.replaceWith(replacement.content.querySelector('.launcher'));
      syncMobileChat();
    }
  }
  const control=event.target.closest('button[data-do]');
  if (!control) return;
  const {do:action,value} = control.dataset;
  if (action==='attach') { control.form.querySelector('input[type=file]').click(); return; }
  if (action==='emoji-picker') {
    const picker=control.form.querySelector('.emoji-picker');
    picker.hidden=!picker.hidden;
    if (!picker.hidden) picker.querySelector('button').focus();
    return;
  }
  if (action==='emoji') {
    const input=control.form.elements.text;
    if (input.value.length+value.length-(input.selectionEnd-input.selectionStart)<=input.maxLength) {
      input.setRangeText(value,input.selectionStart,input.selectionEnd,'end');
      input.dispatchEvent(new Event('input',{bubbles:true}));
    }
    control.parentElement.hidden=true;
    input.focus();
    return;
  }
  if (action==='chat-file') { downloadChatFile(value).catch(showError); return; }
  if (['open-chat','sound','dialog'].includes(action) || action.startsWith('ask-')) {
    if (!audioContext && window.AudioContext) audioContext=new AudioContext();
    audioContext?.resume().catch(()=>{});
  }
  if (action==='clear-history' && !window.confirm('Убрать чаты этого клиента из истории и очереди?')) return;
  if (action==='continue-chat') {
    if (busy) return;
    chatPane='continue';
    if (checkoutOffer) {
      checkoutOffer=undefined;
      render();
    } else {
      control.parentElement.remove();
    }
    app.querySelector('.widget [data-action="message"] textarea')?.focus();
    return;
  }
  if (action==='suggest') {
    const input=app.querySelector('[data-action="message"] textarea');
    input.value=value;
    input.focus();
    return;
  }
  run(async () => {
    switch (action) {
      case 'guest-start':
        await signIn(guestToken());
        dialogId=sessionStorage.getItem('softline-guest-dialog') || undefined;
        chatOpen=Boolean(dialogId);
        break;
      case 'customer-login':
        if (dialogId) sessionStorage.setItem('softline-guest-dialog',dialogId);
        else sessionStorage.removeItem('softline-guest-dialog');
        sessionStorage.removeItem('softline-token');
        session=state=role=tab=result=undefined;
        token='';
        break;
      case 'catalog-day': await act('feed_import',{day:Number(value),request_id:crypto.randomUUID()}); break;
      case 'chat-choice':
        if (value==='Позвать менеджера') {
          await act('handoff',{dialog_id:dialogId,text:value,request_id:control.dataset.requestId ??= crypto.randomUUID()});
          break;
        }
        await act('message',{dialog_id:dialogId,text:value,mode:'rules',request_id:control.dataset.requestId ??= crypto.randomUUID()}); break;
      case 'context-question': {
        const offer=clientContext().viewed;
        const reference=referenceDraft ?? {type:offer.group_name?'group':'offer',id:String(offer.group_id ?? offer.id)};
        await act('message',{dialog_id:dialogId,text:value,mode:'rules',reference:{type:reference.type,id:reference.id},
          request_id:control.dataset.requestId ??= crypto.randomUUID()});
        clearComposer();
        break;
      }
      case 'starter': {
        const viewedOffer=clientContext().viewed;
        if (viewedOffer?.group_name && value==='viewed') {
          await act('message',{text:'Помогите подобрать лицензию для «'+viewedOffer.group_name+'»',mode:'rules',
            viewed_group_id:viewedOffer.group_id,request_id:control.dataset.requestId ??= crypto.randomUUID()});
          break;
        }
        if (viewedOffer?.group_name) clientContext().viewed=undefined;
        const text=value==='viewed'?'Расскажите о товаре «'+viewedOffer.name+'»':control.textContent;
        await act('message',{text,mode:'rules',starter:value,offer_id:viewedOffer?.id,request_id:control.dataset.requestId ??= crypto.randomUUID()});
        break;
      }
      case 'open-chat': chatOpen=launcherSeen=true; break;
      case 'close-chat': chatOpen=false; chatPane=''; checkoutOffer=undefined; break;
      case 'chat-options': chatPane=chatPane==='options'?'':'options'; break;
      case 'chat-history': chatPane='history'; break;
      case 'chat-thread': chatPane=''; break;
      case 'sound':
        soundMuted=!soundMuted;
        localStorage.setItem('softline-chat-muted',String(soundMuted));
        break;
      case 'quote': {
        const m=list(activeDialog()?.messages).find(message=>message.id===value);
        if (!m) break;
        const excerpt=selectedQuote || selectedExcerpt(control);
        quoteDraft={id:value,role:messageRole(m),excerpt,preview:excerpt || (m.text || m.attachment?.name || '').slice(0,240)};
        selectedQuote='';
        getSelection()?.removeAllRanges();
        break;
      }
      case 'clear-quote': quoteDraft=undefined; break;
      case 'clear-file': fileDraft=undefined; break;
      case 'clear-reference': referenceDraft=undefined; break;
      case 'end-chat': chatPane='confirm'; break;
      case 'confirm-resolved': await act('end_chat',{dialog_id:value,confirmed:true}); clearComposer(); chatPane=''; break;
      case 'dialog-result': showDialogResult(activeDialog()); break;
      case 'call-manager': await act('handoff',{dialog_id:value,text:'Позвать менеджера',request_id:control.dataset.requestId ??= crypto.randomUUID()}); chatPane=''; break;
      case 'night-transfer': await act('handoff',{dialog_id:value,simulate_night:true}); chatPane=''; break;
      case 'clear-history': await act('clear_history',{}); clearComposer(); dialogId=undefined; chatPane=''; break;
      case 'logout':
        if (telegramEnabled || telegramSession && session?.role==='jury') {
          try {
            await request('/api/action',{role,action:'telegram_logout'});
          } catch (error) {
            if (error.status!==503) throw error;
          }
        }
        sessionStorage.removeItem('softline-telegram-session');
        telegramSession=undefined;
        telegramEnabled=false;
        resetWorkspace();
        clientContexts.clear();
        sessionStorage.removeItem('softline-guest-dialog');
        sessionStorage.removeItem('softline-guest');
        sessionStorage.removeItem('softline-token');
        token=''; session=state=role=result=dialogId=tab=undefined; break;
      case 'telegram-disconnect':
        state.telegram = await request('/api/action',{role,action:'telegram_disconnect'});
        break;
      case 'refresh': await loadState(); break;
      case 'role':
        if (value===role) break;
        {
          const next = await roleState(value);
          resetWorkspace(); role=value; state=next; result=dialogId=tab=undefined; samplePage=0; sampleQuery='';
        }
        break;
      case 'tab': tab=value; result=undefined; break;
      case 'catalog-page': await loadCatalog(catalogSearch?.query ?? '',Number(value)); break;
      case 'ask-product':
      case 'ask-cart-product':
      case 'ask-order':
      case 'ask-document':
      case 'ask-license': {
        const kind=action.slice(4);
        const offer=kind==='product'?catalogOffer(value):kind==='cart-product'?clientContext().cart.get(value)?.offer:undefined;
        const record=offer ?? list(state[kind==='document'?'documents':kind+'s']).find(row=>String(row.id)===value);
        if (!record) break;
        if (activeDialog()?.status==='resolved') { dialogId=undefined; clearComposer(); }
        if (kind==='product') clientContext().viewed=offer;
        const type=offer?(offer.group_name?'group':'offer'):kind;
        const title=kind==='order'?'Заказ '+record.id+' · '+record.name :
          kind==='document'?(label(record.kind ?? record.type)||'Документ')+' '+(record.number ?? record.id) :
          record.group_name || record.name;
        checkoutOffer=undefined;
        referenceDraft={type,fromCart:kind==='cart-product',id:String(type==='group'?record.group_id:record.id),label:title};
        chatOpen=launcherSeen=true;
        chatPane='';
        break;
      }
      case 'select-product':
      case 'choose-group': {
        const offer=catalogOffer(value);
        if (!offer) break;
        clientContext().viewed=offer;
        if (action!=='select-product') {
          if (activeDialog()?.status==='resolved') { dialogId=undefined; clearComposer(); }
          chatOpen=launcherSeen=true;
          chatPane='';
        }
        if (action==='choose-group') {
          referenceDraft=undefined;
          const payload={text:'Подобрать лицензию',mode:'rules',
            reference:{type:'group',id:String(offer.group_id)},request_id:control.dataset.requestId ??= crypto.randomUUID()};
          if (dialogId) payload.dialog_id=dialogId;
          await act('message',payload);
        }
        break;
      }
      case 'cart-add': {
        const {cart}=clientContext();
        const offer=catalogOffer(value);
        if (!offer) break;
        if (cart.size>=20) throw Error('В корзине уже 20 позиций. Уберите одну, чтобы добавить товар.');
        cart.set(value,{offer,quantity:quantityOf(offer)});
        break;
      }
      case 'chat-cart': {
        const {cart}=clientContext();
        const offer=list(activeDialog()?.messages).flatMap(message=>list(message.offers))
          .findLast(option=>String(option.id)===value && option.group_recommendation && option.available);
        if (!offer || cart.has(value)) break;
        if (cart.size>=20) throw Error('В корзине уже 20 позиций. Уберите одну, чтобы добавить товар.');
        cart.set(value,{offer,quantity:offer.suggested_quantity});
        break;
      }
      case 'cart-plus':
      case 'cart-minus': {
        const {cart}=clientContext();
        const line=cart.get(value);
        if (!line) break;
        if (action==='cart-plus') line.quantity=Math.min(line.quantity+1,line.offer.max_quantity || 100000);
        else if (line.quantity<=quantityOf(line.offer)) cart.delete(value);
        else line.quantity--;
        break;
      }
      case 'cart-remove': clientContext().cart.delete(value); break;
      case 'workspace': training=value==='training'; tab=result=undefined; clearComposer(); break;
      case 'dialog': dialogId=value; result=undefined; clearComposer(); break;
      case 'open-dialog': clearComposer(); checkoutOffer=undefined; dialogId=value; chatOpen=true; chatPane=''; result=undefined; break;
      case 'new-dialog': clearComposer(); checkoutOffer=undefined; dialogId=''; chatOpen=true; chatPane=''; result=undefined; break;
      case 'chat-offer': {
        checkoutOffer=list(activeDialog()?.messages).flatMap(m=>list(m.offers)).findLast(o=>String(o.id)===value);
        break;
      }
      case 'cancel-checkout': checkoutOffer=undefined; break;
      case 'sample-page': samplePage=Number(value); break;
      case 'price-request': {
        await act('price_request',{offer_id:value,dialog_id:activeDialog()?.status==='resolved'?'':dialogId,
          request_id:control.dataset.requestId ??= crypto.randomUUID()});
        chatOpen=true;
        break;
      }
      case 'download': await act('download',{document_id:value}); break;
      case 'copy-license':
      case 'download-license': {
        const offer=list(state.licenses).find(item=>String(item.id)===value);
        if (!offer?.key) break;
        if (action==='copy-license') {
          try { await navigator.clipboard.writeText(offer.key); }
          catch { throw Error('Не удалось скопировать ключ. Выделите его и скопируйте вручную.'); }
          errorText='Ключ скопирован.';
        } else {
          const href=URL.createObjectURL(new Blob([displayText(offer.name)+'\n'+offer.key+'\nУчебные сведения о лицензии.\n'],{type:'text/plain;charset=utf-8'}));
          const link=document.createElement('a');
          link.href=href;
          link.download='license-demo.txt';
          link.click();
          setTimeout(()=>URL.revokeObjectURL(href),1000);
        }
        break;
      }
      case 'telegram-settings': telegramOpen=!telegramOpen; break;
      case 'browser-alerts':
        if (!window.Notification || !navigator.serviceWorker) throw Error('Этот браузер не поддерживает уведомления.');
        if (alertsEnabled()) localStorage.removeItem(managerKey()+'alerts');
        else if (await Notification.requestPermission()==='granted') localStorage.setItem(managerKey()+'alerts','on');
        else throw Error('Разрешите уведомления в настройках сайта.');
        break;
      case 'availability': await act(action,{available:value==='true'}); break;
      case 'rate': await act('review',{dialog_id:dialogId,message_id:value,rating:control.dataset.rating}); break;
      case 'claim':
      case 'resolve': await act(action,{dialog_id:value}); break;
      case 'template_delete':
        if (!window.confirm('Удалить черновик?')) break;
        await act(action,{template_id:value}); break;
      case 'template_publish':
      case 'template_rollback': await act(action,{template_id:value}); break;
    }
  },control,()=>{
    if (action==='chat-offer') {
      app.querySelector('[data-action="document"] input[type="number"], [data-action="document"] button[name="kind"]')?.focus();
      return;
    }
    if (action==='cancel-checkout') {
      app.querySelector('.widget textarea')?.focus();
      return;
    }
    if (action==='catalog-page') {
      app.querySelector('.catalog-search input')?.focus();
      return;
    }
    if (['quote','clear-quote','clear-file','clear-reference','chat-thread'].includes(action)) {
      app.querySelector('.composer-line textarea')?.focus();
      return;
    }
    if (action==='dialog' || action==='claim') {
      const thread=app.querySelector('.staff-thread');
      thread?.closest('.panel')?.scrollIntoView({block:'start'});
      if (thread) thread.scrollTop=thread.scrollHeight;
    }
    if (chatOpen && (action.startsWith('ask-') || ['open-chat','choose-group','starter','new-dialog','open-dialog'].includes(action))) {
      (app.querySelector('.widget textarea') ?? app.querySelector('.widget-head button'))?.focus();
      const thread=app.querySelector('.thread'); if (thread) thread.scrollTop=thread.scrollHeight;
      return;
    }
    if (action==='chat-choice' || action==='context-question' || action==='price-request') {
      const thread=app.querySelector('.thread');
      if (thread) thread.scrollTop=thread.scrollHeight;
      (app.querySelector('[data-do="chat-choice"]') ?? app.querySelector('.widget textarea'))?.focus({preventScroll:true});
      return;
    }
    if (action==='close-chat') app.querySelector('.launcher')?.focus();
    const buttons=[...document.querySelectorAll('button[data-do]')];
    const focus=buttons.find(button=>button.dataset.do===action && button.dataset.value===value);
    if (focus) focus.focus();
    else if (['cart-add','cart-minus','cart-plus'].includes(action)) {
      buttons.find(button=>button.dataset.value===value && ['cart-minus','cart-add','cart-plus'].includes(button.dataset.do))?.focus();
    }
  });
});
app.addEventListener('input',event=>{
  if (event.target.matches('[data-action="message"] textarea')) buyerDraft=event.target.value;
  if (event.target.matches('[data-action="reply"] textarea')) staffDraft=event.target.value;
});
app.addEventListener('change',event=>{
  if (event.target.matches('#jury-stand select[name="mode"]')) { buyerMode=event.target.value; render(); }
  if (event.target.matches('#jury-stand select[name="client_profile"]')) {
    const control=event.target;
    run(async ()=>{
      await act('buyer_select',{buyer_id:control.value});
      resetWorkspace();
      dialogId=result=undefined;
    },control,()=>app.querySelector('[name="client_profile"]')?.focus());
  }
  if (event.target.matches('.composer-line input[type=file]')) {
    const file=event.target.files[0];
    if (!file) return;
    if (!file.size || file.size>1048576 || file.name.length>200) {
      showError(Error('Выберите непустой файл до 1 МБ с коротким именем.'));
      return;
    }
    fileDraft=file;
    render();
    app.querySelector('.composer-line textarea')?.focus();
  }
});
document.addEventListener('keydown',event=>{
  if (event.key==='Enter' && !event.altKey && !event.isComposing && event.keyCode!==229 &&
      event.target.matches('form[data-action="message"] textarea,form[data-action="reply"] textarea')) {
    event.preventDefault();
    if (!event.repeat && !busy && (event.target.value.trim() || fileDraft)) event.target.form.requestSubmit();
    return;
  }
  if (event.key==='Escape' && chatOpen && !document.querySelector('.dialog-result[open]')) app.querySelector('[data-do="close-chat"]')?.click();
});
// Update the open conversation without replacing a draft or stealing focus.
let checkingChat=false;
setInterval(async ()=>{
  if (app.querySelector('.manager-list') && !checkingChat && !busy) {
    checkingChat=true;
    const before=state;
    try {
      const next=await roleState(role);
      if (before!==state || busy || !app.querySelector('.manager-list')) return;
      const changed=list(next.dialogs).filter(d=>{
        const prior=list(before.dialogs).find(x=>x.id===d.id);
        return !prior || revision(prior)!==revision(d);
      });
      const added=list(next.queue).filter(x=>!list(before.queue).some(y=>x.id===y.id));
      if (alertsEnabled() && (added.length || changed.some(d=>document.hidden || d.id!==dialogId))) {
        const worker=await navigator.serviceWorker.getRegistration();
        if (worker) await worker.showNotification('Новое обращение или сообщение',{tag:'manager-queue'});
      }
      if (before!==state || busy) return;
      const previous=activeDialog();
      state=next;
      const current=activeDialog();
      if (current && !document.hidden) localStorage.setItem(readKey(current),revision(current));
      const node=app.querySelector('.manager-list');
      if (!node) return;
      const markup=managerList(list(next.dialogs));
      if (node.outerHTML!==markup && !node.contains(document.activeElement)) node.outerHTML=markup;
      if (JSON.stringify(previous)!==JSON.stringify(current)) render();
    } catch (error) { if (!session) showError(error); }
    finally { checkingChat=false; }
    return;
  }
  const previous=activeDialogSafe();
  if (!previous || checkingChat || busy || document.hidden) return;
  const before=state;
  checkingChat=true;
  try {
    const next=await request('/api/chat?'+new URLSearchParams({role,id:previous.id}));
    if (busy || before!==state || activeDialogSafe()!==previous) return;
    const updated=list(next.dialogs).find(d=>d.id===previous.id);
    if (!updated) { await loadState(); dialogId=result=undefined; clearComposer(); render(); return; }
    if (JSON.stringify(previous)===JSON.stringify(updated)) return;
    state={...state,dialogs:list(state.dialogs).map(d=>d.id===updated.id?updated:d)};
    if (list(updated.messages).length>list(previous.messages).length) notifyMessage();
    if (storefront()) {
      const thread=app.querySelector('.thread');
      const nearEnd=thread.scrollHeight-thread.scrollTop-thread.clientHeight<48;
      thread.innerHTML=conversation(updated);
      if (nearEnd) thread.scrollTop=thread.scrollHeight;
      const footer=app.querySelector('.chat-foot');
      const markup=document.createElement('template');
      markup.innerHTML=buyerChat(updated);
      if (updated.status==='resolved') footer.replaceWith(markup.content.querySelector('.chat-foot'));
      else {
        footer.querySelector('.handoff')?.remove();
        footer.querySelector('.quick-actions')?.remove();
        const suggestions=markup.content.querySelector('.chat-foot .quick-actions');
        if (suggestions) footer.prepend(suggestions);
        const status=markup.content.querySelector('.handoff');
        if (status) footer.prepend(status);
      }
    } else if (updated.status!==previous.status || JSON.stringify(updated.linked_orders)!==JSON.stringify(previous.linked_orders)) {
      const draft=app.querySelector('[data-action=reply] textarea')?.value;
      render();
      const input=app.querySelector('[data-action=reply] textarea');
      if (draft && input) input.value=draft;
    } else {
      const thread=app.querySelector('.staff-thread');
      const nearEnd=thread.scrollHeight-thread.scrollTop-thread.clientHeight<48;
      thread.innerHTML=conversation(updated,true);
      if (nearEnd) thread.scrollTop=thread.scrollHeight;
    }
  } catch (error) { if (!session) showError(error); }
  finally { checkingChat=false; }
},4000);
function activeDialogSafe() {
  if (!state || (storefront() ? !chatOpen : !app.querySelector('.staff-thread'))) return;
  return activeDialog();
}
if (token) run(()=>signIn(token));
else render();
const fieldHint=document.createElement('div');
fieldHint.className='field-hint';
fieldHint.hidden=true;
fieldHint.setAttribute('aria-hidden','true');
document.body.append(fieldHint);
function showFieldHint(event) {
  const field=event.target.closest('[aria-description]');
  if (!field) { fieldHint.hidden=true; return; }
  fieldHint.textContent=field.getAttribute('aria-description');
  fieldHint.hidden=false;
  const rect=field.getBoundingClientRect();
  fieldHint.style.left=Math.max(8,Math.min(rect.left,document.documentElement.clientWidth-fieldHint.offsetWidth-8))+'px';
  fieldHint.style.top=(rect.top>=fieldHint.offsetHeight+8?rect.top-fieldHint.offsetHeight-4:rect.bottom+4)+'px';
}
app.addEventListener('pointerover',showFieldHint);
app.addEventListener('pointerout',()=>fieldHint.hidden=true);
app.addEventListener('focusin',event=>{if(event.target.matches(':focus-visible')) showFieldHint(event);});
app.addEventListener('focusout',()=>fieldHint.hidden=true);
document.addEventListener('scroll',()=>fieldHint.hidden=true,true);
document.addEventListener('keydown',()=>fieldHint.hidden=true);
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(()=>{});
