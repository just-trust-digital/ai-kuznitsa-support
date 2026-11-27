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
let session, role, state, dialogId, result, tab, busy = false, samplePage = 0, sampleQuery = '', selectedOffer = '';
let errorText = '';
let standExpanded = false;
let catalogExpanded = false;
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
      viewed: sample(offers.slice(0,8),1)[0],
      licenses: role==='buyer' ? sample(priced,2+Math.floor(Math.random()*2)) : [],
      cart: new Map((role==='buyer' ? sample(priced,1+Math.floor(Math.random()*2)) : [])
        .map(offer => [String(offer.id),offer])),
    });
  }
  return clientContexts.get(key);
}
let buyerMode = 'local';
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
const openProducts = new Set();
let chatPane='', quoteDraft, fileDraft, staffDraft='', audioContext;
let soundMuted=localStorage.getItem('softline-chat-muted')==='true';
const clearComposer=()=>{ buyerDraft=staffDraft=''; quoteDraft=fileDraft=undefined; };
const chatIcons={
  close:'<path d="m6 6 12 12M18 6 6 18"/>',
  more:'<path d="M12 5h.01M12 12h.01M12 19h.01" stroke-width="3.5"/>',
  reply:'<path d="M9 5 3 11l6 6v-4h4c4 0 6 2 8 5 0-7-3-9-8-9H9Z"/>',
  attach:'<path d="m8 13 6-6a3 3 0 0 1 4 4l-8 8a5 5 0 0 1-7-7l9-9m-5 9a1 1 0 0 0 2 2l7-7"/>',
  emoji:'<circle cx="12" cy="12" r="9"/><path d="M8 14c2 3 6 3 8 0M9 9h.01M15 9h.01"/>',
  send:'<path d="m3 10 18-7-7 18-3-8-8-3Zm8 3 10-10"/>'
};
const icon=name=>'<svg aria-hidden="true" viewBox="0 0 24 24">'+chatIcons[name]+'</svg>';
const iconButton=(name,title,action,value='')=>'<button type="'+(action?'button':'submit')+'" class="chat-icon" aria-label="'+title+'" title="'+title+'" '+
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
  chatOpen = launcherSeen = standExpanded = catalogExpanded = training = false;
  clearComposer();
  chatPane=selectedOffer='';
  openProducts.clear();
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
      ...(session?.role==='jury' && (data?.role==='guest' || new URL(path,location.href).searchParams.get('role')==='guest')
        ? {'X-Stand-Guest':guestToken()} : {})
    },body:data?JSON.stringify(data):undefined,signal:AbortSignal.timeout(25000)});
  } catch { throw Error('Связь со стендом прервалась. Проверьте соединение и повторите.'); }
  const payload = await response.json().catch(()=>({}));
  if (response.status===401) {
    sessionStorage.removeItem('softline-token');
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
async function loadState() { state = await request('/api/state?role='+encodeURIComponent(role)); }
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
  }
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
  app.innerHTML = '<h1>Поддержка Softline</h1><section class="panel narrow"><h2>Вход</h2>'+statusMarkup()+form('login',
    field('Токен доступа','token','','type="password" required autocomplete="off" autofocus')+
    '<div class="login-actions">'+submit('Войти')+button('Продолжить как гость','guest-start','')+'</div>')+'</section>';
  syncMobileChat();
  applyFieldHints();
}
function statusMarkup() {
  return '<p id="status" class="status" role="status" tabindex="-1">'+escape(errorText)+'</p>';
}
function render() {
  if (!session) { renderLogin(); return; }
  nav.innerHTML = (session.role==='jury' ? choices(list(session.roles).map(id=>[id,label(id)]),'role',role) : '')
    +button('Обновить','refresh','')+(session.role==='guest'?button('Войти в кабинет','customer-login',''):button('Выйти','logout',''));
  if (!state) { app.innerHTML='<h1>Рабочее место</h1>'+statusMarkup(); return; }
  const page = storefront() ? buyerPage() : staffPage();
  app.innerHTML = '<h1>'+escape(page.title)+'</h1>'+statusMarkup()+page.html;
  syncMobileChat();
  applyFieldHints();
}
const phoneScreen=matchMedia('(max-width:540px)');
function syncMobileChat() {
  const widget=app.querySelector('.widget');
  const fullScreen=Boolean(widget && phoneScreen.matches);
  for (const node of [document.querySelector('header'),document.querySelector('footer'),...app.children]) {
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
    const quote=m.quote?'<blockquote><b>'+escape(label(m.quote.role))+'</b><div>'+escape(displayText(m.quote.text))+'</div></blockquote>':'';
    const file=m.attachment?button(m.attachment.name,'chat-file',id,'class="chat-file"'):'';
    const reply=id && dialog.status!=='resolved' && !training && role!=='developer' && who!=='system'?iconButton('reply','Ответить на сообщение','quote',id):'';
    return '<article class="bubble '+(who === (staff?role:'buyer')?'mine':'')+'"><small>'+escape(label(speaker))+'</small>'+quote+
      '<div class="message-text" data-message="'+attr(id)+'">'+escape(displayText(m.text))+'</div>'+file+offers.slice(0,1).map(o=>chatOffer(o,staff)).join('')+
      (offers.length>1?'<details><summary>Ещё варианты ('+(offers.length-1)+')</summary>'+offers.slice(1).map(o=>chatOffer(o,staff)).join('')+'</details>':'')+
      (safeUrl(m.source)?'<a href="'+attr(safeUrl(m.source))+'" target="_blank" rel="noreferrer">Источник</a>':'')+
      (staff && training && id && (who==='assistant'||who==='bot')?
        '<div class="row">'+button('+' ,'rate',id,'aria-label="Ответ полезен" data-rating="+"')+button('−','rate',id,'aria-label="Ответ не помог" data-rating="-"')+'</div>':'')+
      '<div class="message-meta">'+timeMarkup(m.at)+reply+'</div></article>';
  },'Сообщений пока нет.')+'</div>';
}
function offerPrice(offer, showComparison = true) {
  if (!offer.available || offer.price == null) return 'Цена по запросу';
  const current=escape(num(offer.price))+' '+escape(offer.currency);
  return showComparison && Number(offer.old_price)>Number(offer.price) && Number(offer.price)>0
    ? '<span class="reduced-price">'+current+' <span>Цена снижена</span></span> <del>'+escape(num(offer.old_price))+' '+escape(offer.currency)+'</del>' : current;
}
function offerAction(o, action) {
  if (role==='guest' && o.available) return button('Войти для оформления','customer-login','');
  return o.available ? button('Счёт или КП',action,o.id,'data-available="true"') : button('Запросить цену','price-request',o.id);
}
function chatOffer(o,staff) {
  return item(displayText(o.name),'<p>'+offerPrice(o)+'</p>'+quantityTerms(o)+offerPreview(o)+(staff?'':offerAction(o,'chat-offer')));
}
function offerPreview(o) {
  const url=safeUrl(o.url);
  const term=o.params?.['Срок действия']?.[0];
  const delivery=o.params?.['Тип поставки']?.[0];
  const details=[o.description,term && term!=='Не определено'?'Срок лицензии · '+term:'',delivery?'Поставка · '+delivery:''].filter(Boolean);
  if (!details.length) details.push(o.available?'Доступно к заказу':'Цена и наличие по запросу');
  return '<details class="offer-preview"><summary>Карточка товара</summary><div>'+
    details.map(s=>'<p>'+escape(displayText(s))+'</p>').join('')+
    (url?'<a href="'+attr(url)+'" target="_blank" rel="noreferrer">Открыть на сайте</a>':'')+'</div></details>';
}
function quantityTerms(o) {
  const capacity=o.params?.['К-во пользователей до']?.find(v=>/^\d+$/.test(v));
  const units=o.min_quantity===o.max_quantity && o.min_quantity ? 'Заказ: '+num(o.min_quantity)+' шт.' :
    o.min_quantity ? 'Заказ от '+num(o.min_quantity)+(o.max_quantity?' до '+num(o.max_quantity):'')+' шт.' : '';
  return [capacity?'До '+num(Number(capacity))+' пользователей':null,units].filter(Boolean).map(text=>'<p>'+text+'</p>').join('');
}
function buyerPage() {
  const d = activeDialog();
  const current = tab ?? 'catalog';
  const tabs = [['catalog','Каталог'],['cart','Корзина'],...(role==='buyer'?[['orders','Заказы'],['docs','Документы']]:[])];
  const modules = {catalog:catalogPage,cart:cartPage,orders:ordersPage,docs:docsPage};
  const jury = session.role==='jury';
  const settings = jury && role==='buyer' ? '<aside class="panel session"><h2>Сессия</h2><details id="jury-stand" '+(standExpanded?'open':'')+'><summary>Настроить сессию</summary><label>Как отвечает помощник'+choose('mode',modes,buyerMode)+'</label>'+standPage()+'</details></aside>' : '';
  const owner=state.customer?.company_name || state.customer?.full_name;
  const store = '<div class="shop">'+(owner?'<p>'+escape(owner)+'</p>':'')+'<h2 id="context-heading">Контекст переписки</h2>'+
    '<nav class="context-tabs" aria-labelledby="context-heading">'+choices(tabs,'tab',current)+'</nav>'+modules[current]()+'</div>';
  const launcher = '<button class="launcher '+(launcherSeen?'':'invite')+'" type="button" data-do="open-chat" aria-label="Открыть чат поддержки"><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M5 4h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2h-8l-6 4v-4H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z"/><path d="M7 9h10M7 13h7"/></svg></button>';
  return {title:'ИИ ассистент поддержки',html:'<div class="'+(settings?'grid':'')+'">'+settings+store+'</div>'+(chatOpen?buyerChat(d):launcher)};
}
function handoffLabel(d) {
  if (d?.status==='resolved') return 'Чат закрыт';
  if (d?.status==='queued') return 'В очереди к менеджеру';
  if (d?.status==='assigned') return list(d.messages).some(m=>messageRole(m)===d.assignment || m.joined===d.assignment) ? 'Менеджер подключился' : 'Чат передан менеджеру';
  return '';
}
function buyerChat(d) {
  const viewedOffer = (tab ?? 'catalog')==='catalog' ? clientContext().viewed : undefined;
  const greeting = viewedOffer ? 'Здравствуйте! Рассказать о товаре «'+viewedOffer.name+'»?' : 'Здравствуйте! Ищете конкретную программу или помочь с выбором?';
  const starters = (viewedOffer?button('Расскажите о товаре','starter','viewed'):button('Ищу программу','starter','search'))+button('Помогите с выбором','starter','choose');
  const menu='<div class="chat-options" id="chat-options" '+(chatPane==='options'?'':'hidden')+'>'+
    button('Новый чат','new-dialog','')+button(soundMuted?'Включить звук':'Выключить звук','sound','')+
    (d && d.status!=='resolved'?button('Завершить консультацию','end-chat',d.id):'')+button('Мои обращения','chat-history','')+'</div>';
  const history=chatPane==='history'?'<div class="chat-history"><div class="history-heading"><h3>Мои обращения</h3>'+button('К переписке','chat-thread','')+'</div>'+chatHistory()+'</div>':'';
  const thread = d?conversation(d):'<p class="bubble">'+escape(displayText(greeting))+'</p>';
  const status = handoffLabel(d);
  return '<section class="widget" aria-label="Помощник Softline"><div class="widget-head"><img src="/softline-solutions.png" alt=""><h2>Помощник Softline</h2>'+
    iconButton('more','Опции чата','chat-options')+iconButton('close','Закрыть чат','close-chat')+'</div>'+menu+history+
    '<div class="thread" '+(history?'hidden':'')+'>'+thread+'</div><div class="chat-foot" '+(history?'hidden':'')+'><p class="chat-error" role="alert"></p>'+
    (status?'<p class="handoff" role="status">'+escape(status)+'</p>':'')+
    (role==='guest' && d?.decision?.next_action==='login'?
      (session.role==='jury'?button('Переключиться на клиента','role','buyer'):button('Войти в кабинет','customer-login','')):'')+
    (d?.status==='resolved'?button('Начать новый чат','new-dialog',''):(!d?'<div class="quick-actions">'+starters+'</div>':'')+composer('message',buyerDraft))+
    '</div></section>';
}
function composer(action,draft,id='') {
  const quote=quoteDraft?'<div class="composer-note"><blockquote><b>'+escape(label(quoteDraft.role))+'</b><div>'+escape(displayText(quoteDraft.preview))+'</div></blockquote>'+
    iconButton('close','Убрать цитату','clear-quote')+'</div>':'';
  const file=fileDraft?'<div class="composer-note"><span>'+escape(fileDraft.name)+'</span>'+iconButton('close','Убрать файл','clear-file')+'</div>':'';
  return form(action,(id?hidden('dialog_id',id):'')+quote+file+
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
function catalogPage() {
  const offers = result?.offers && (result.query!==undefined || selectedOffer) ? list(result.offers) : list(state.offers).slice(0,8);
  const context = clientContext();
  return panel('Каталог программ',form('search',field('Товар или задача','query',result?.query ?? '', 'required maxlength="220"')+submit('Найти'))+
    '<p class="muted">Помощник учитывает выделенный товар. Чтобы выбрать другой, раскройте «О товаре».</p>'+
    rows(offers, offer => {
      const id = String(offer.id);
      const url = safeUrl(offer.url);
      const approved=activeDialog()?.approved_discount;
      const minimum=quantityOf(offer), maximum=offer.max_quantity || 100000;
      const viewed=String(context.viewed?.id)===id;
      return '<article class="product" data-viewed="'+viewed+'"><h3>'+escape(displayText(offer.name))+'</h3><p><b>'+offerPrice(offer,false)+'</b></p>'+
        '<details data-product="'+attr(id)+'" '+(openProducts.has(id)?'open':'')+'><summary>О товаре</summary>'+
        (offer.description && offer.description!==offer.name?'<p>'+escape(displayText(offer.description))+'</p>':'')+quantityTerms(offer)+'</details>'+
        '<div class="row">'+(url?'<a href="'+attr(url)+'" data-product-link="'+attr(id)+'" target="_blank" rel="noreferrer">Карточка товара</a>':'')+
        (context.cart.has(id)?button('В корзине','tab','cart'):button('Добавить в корзину','cart-add',id))+'</div>'+
        (offer.available && String(offer.id)===selectedOffer ? form('document',hidden('offer_id',offer.id)+hidden('expected_price',offer.price)+field('Количество','quantity',minimum,'type="number" min="'+minimum+'" max="'+maximum+'" required')+
          (approved?.offer_id===offer.id?'<p>Согласованная скидка '+num(approved.rate)+'%</p>':'')+
          '<div class="row"><button class="primary" name="kind" value="invoice">Получить счёт</button><button name="kind" value="proposal">Получить КП</button></div>'):'')+'</article>';
    },'Товары не найдены. Уточните запрос.'));
}
function cartPage() {
  const cart = [...clientContext().cart.values()];
  return panel('Корзина', rows(cart, offer => item(displayText(offer.name),
    '<p>'+num(quantityOf(offer))+' шт.</p>'+button('Убрать из корзины','cart-remove',offer.id)),
  'В корзине пока ничего нет. Добавьте товар из каталога.'));
}
function ordersPage() {
  return panel('Заказы',rows(list(state.orders),o=>item(o.name ?? o.product_name ?? o.id,
    '<p>'+escape(o.id)+'</p>'+button('Проверить статус','order',o.id)),'Заказов пока нет.')+
    (result?.order ? '<p class="note">'+escape(result.order.status)+' · '+escape(result.order.license_status ?? result.order.delivery_date ?? '')+'</p>':'')+
    '<h3>Лицензии</h3>'+rows(clientContext().licenses, offer=>'<p>'+escape(displayText(offer.name))+'</p>','Лицензий пока нет.'));
}
function docsPage() {
  return panel('Документы',rows(list(state.documents),d=>item((label(d.kind ?? d.type)||'Документ')+' '+(d.number ?? d.id),
    '<p>'+escape(d.name ?? d.product_name ?? d.filename)+'</p><p>'+escape(num(d.total))+' '+escape(d.currency)+'</p>'+button('Скачать','download',d.id)),'Документов пока нет. Выберите товар в каталоге.'));
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
function queuePage() {
  const dialogs = list(state.dialogs);
  const d = activeDialog();
  const manager = state.managers?.[role] ?? list(state.managers).find(m=>(m.id ?? m.role)===role);
  const learning=training || role==='developer';
  let queue = manager && !learning ? '<p>'+(manager.available?'На линии':'Не на линии')+'</p>'+button(manager.available?'Уйти с линии':'Выйти на линию','availability',!manager.available):'';
  queue += rows(dialogs,x=>'<button type="button" class="order" data-do="dialog" data-value="'+attr(x.id)+'" aria-pressed="'+(d?.id===x.id)+'"><b>'+escape(selectDialogLabel(x))+'</b><small>'+escape(label(x.status))+' · '+escape(label(x.assignment ?? 'Без назначения'))+'</small></button>','В очереди пока нет обращений.');
  if (!learning) queue += list(state.queue).filter(x=>!dialogs.some(y=>y.id===x.id)).map(x=>item('В общей очереди',
    '<p>'+escape(x.id)+' · '+escape(x.route?.reason ?? x.reason)+'</p>'+button('Взять обращение','claim',x.id))).join('');
  let detail = conversation(d,true);
  if (d) {
    detail = '<p class="muted">'+escape(label(d.status))+' · '+escape(label(d.assignment ?? 'Без назначения'))+'</p>'+
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
  return '<div class="grid">'+panel('Очередь чатов',queue)+panel('Переписка',detail)+'</div>';
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
    '<p class="muted">Источник '+escape(value==='xml'?'Каталог':value)+'</p>';
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
  return panel('Журнал',handoffStats()+stats([['Оценок',num(reviews.length)]])+rows(journal,e=>item(e.title ?? actions[e.action] ?? e.action ?? e.event,'<p>'+escape(e.at ?? e.created_at)+' '+escape(label(e.role ?? e.actor))+'</p>'),'Записей пока нет.')+
    '<h3>Оценки ответов</h3>'+rows(reviews,r=>item((r.rating ?? r.label ?? 'Разметка')+' · '+(r.dialog_id ?? r.sample_id ?? ''),
      '<p>'+escape(r.note)+' '+escape(r.at ?? r.created_at)+'</p>'),'Оценок пока нет.'));
}
function handoffStats() {
  const data=state.handoff_stats;
  if (!data || !Number.isInteger(data.dialogs)) return '';
  const count=data.dialogs ?? 0;
  const first=data.first_request ?? 0;
  const dateTime=new Intl.DateTimeFormat('ru-RU',{dateStyle:'short',timeStyle:'short',timeZone:'Europe/Moscow'});
  const date=value=>value && Number.isFinite(Date.parse(value)) ? dateTime.format(new Date(value)) : '';
  const start=date(data.period_start);
  const end=date(data.period_end);
  const share=count ? new Intl.NumberFormat('ru-RU',{style:'percent',maximumFractionDigits:1}).format(first/count) : 'Нет обращений';
  const context=session.role==='jury' ? 'Учебные обращения' : 'Обращения стенда';
  return '<section class="form"><h3>Запросы менеджера и передачи</h3><p class="muted">'+context+
    (start && end?' · '+escape(start)+' — '+escape(end)+' МСК':'')+'</p>'+
    stats([['В первой реплике',num(first)+' из '+num(count)+' · '+share],['Запросили позже',num(data.later_request ?? 0)],['Переданы автоматически',num(data.automatic ?? 0)]])+'</section>';
}
function standPage() {
  const feed = state.feed ?? {};
  const changes = feed.changes ?? {};
  const counts = feed.metrics ?? state.metrics?.feed ?? (Array.isArray(changes)?{updated:changes.length}:changes);
  const providerNames={jev:'Jev',cloud:'Облачная модель',nimble:'Nimble',local_llm:'Локальная модель'};
  const usage=Object.entries(state.usage ?? {}).filter(([,value])=>value.calls>0);
  return '<details class="form catalog-settings" id="catalog-settings" '+(catalogExpanded?'open':'')+'><summary>Версии каталога и изменения цен</summary>'+
    stats([['Версия каталога',num(feed.day)],['Товаров',num(feed.count)],...Object.entries({added:'Добавлено',removed:'Удалено',updated:'Обновлено'})
    .filter(([key])=>counts[key]!=null).map(([key,title])=>[title,num(Array.isArray(counts[key])?counts[key].length:counts[key])])])+
    '<div class="row">'+button('Загрузить каталог за день 1','feed_import',1)+button('Загрузить каталог за день 2','feed_import',2)+'</div>'+
    (Array.isArray(changes)&&changes.length?changes.map(change=>item(change.id,'<p>Было '+num(change.before)+' · Стало '+num(change.price)+'</p>')).join(''):'')+'</details>'+
    (usage.length?'<details class="form"><summary>Запросы к моделям</summary>'+usage.map(([key,value])=>item(providerNames[key] ?? key,'<p>Запросов '+num(value.calls)+' · С учётом токенов '+num(value.metered_calls)+'</p>')).join('')+'</details>':'')+
    (session.role==='jury' && role==='buyer'?'<div class="form"><h3>Профиль клиента</h3>'+choices(Object.entries(state.client_profiles ?? {}),'buyer_select',state.buyer_id)+
      '<div class="row">'+button('Очистить историю обращений','clear-history','')+'</div></div>':'');
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
    if (action==='filter') { sampleQuery=p.query.trim(); samplePage=0; return; }
    if (action==='message') {
      p.mode=buyerMode;
      if (dialogId) p.dialog_id=dialogId;
      const context = clientContext();
      if (tab==='cart') {
        p.page_context={type:'basket',items:[...context.cart.values()].map(offer=>({offer_id:String(offer.id),quantity:quantityOf(offer)}))};
      } else if ((!tab || tab==='catalog') && context.viewed) {
        p.viewed_offer_id=String(context.viewed.id);
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
      if (result.document) { chatOpen=false; tab='docs'; selectedOffer=''; }
      else if (result.dialog) chatOpen=true;
      return;
    }
    await act(action,p);
    if (action==='copilot' && !training) staffDraft=result.text ?? '';
    if (message) {
      clearComposer();
      if (action==='message' && messageRole(list(result.dialog?.messages).at(-1) ?? {})==='assistant') notifyMessage();
    }
    if (action==='search') result={...result,query:p.query};
  }, event.submitter, ()=>{
    app.querySelector('[data-action="'+action+'"] textarea')?.focus();
    const thread=app.querySelector(action==='reply'?'.staff-thread':'.thread');
    if (thread) thread.scrollTop=thread.scrollHeight;
  });
});
app.addEventListener('toggle',event=>{
  if (event.target.id==='jury-stand') standExpanded=event.target.open;
  if (event.target.id==='catalog-settings') catalogExpanded=event.target.open;
},true);
const catalogOffer = id => [...list(result?.offers),...list(state.offers)].find(offer=>String(offer.id)===id);
app.addEventListener('click',event=>{
  const summary=event.target.closest('details[data-product] > summary');
  const link=event.target.closest('[data-product-link]');
  if (summary) {
    event.preventDefault();
    const {product:id}=summary.parentElement.dataset;
    if (openProducts.has(id)) openProducts.delete(id);
    else {
      openProducts.add(id);
      clientContext().viewed=catalogOffer(id);
    }
    render();
    [...app.querySelectorAll('details[data-product]')].find(details=>details.dataset.product===id)?.querySelector('summary')?.focus();
  } else if (link) {
    clientContext().viewed=catalogOffer(link.dataset.productLink);
    for (const card of app.querySelectorAll('.product')) card.dataset.viewed=String(card.contains(link));
  }
});
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
  if (chatOpen && storefront() && !event.target.closest('.widget')) {
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
  if (['open-chat','sound','dialog'].includes(action)) {
    if (!audioContext && window.AudioContext) audioContext=new AudioContext();
    audioContext?.resume().catch(()=>{});
  }
  if (action==='clear-history' && !window.confirm('Убрать чаты этого клиента из истории и очереди?')) return;
  if (action==='end-chat' && !window.confirm('Завершить консультацию? Переписка сохранится в «Моих обращениях».')) return;
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
        chatOpen=true;
        break;
      case 'customer-login':
        if (dialogId) sessionStorage.setItem('softline-guest-dialog',dialogId);
        else sessionStorage.removeItem('softline-guest-dialog');
        sessionStorage.removeItem('softline-token');
        session=state=role=tab=result=undefined;
        token='';
        break;
      case 'starter': {
        const viewedOffer=clientContext().viewed;
        const text=value==='viewed'?'Расскажите о товаре «'+viewedOffer.name+'»':control.textContent;
        await act('message',{text,mode:'rules',starter:value,offer_id:viewedOffer?.id,request_id:control.dataset.requestId ??= crypto.randomUUID()});
        break;
      }
      case 'open-chat': chatOpen=launcherSeen=true; break;
      case 'close-chat': chatOpen=false; chatPane=''; break;
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
      case 'end-chat': await act('end_chat',{dialog_id:value}); clearComposer(); chatPane=''; break;
      case 'clear-history': await act('clear_history',{}); clearComposer(); dialogId=undefined; chatPane=''; break;
      case 'logout':
        resetWorkspace();
        clientContexts.clear();
        sessionStorage.removeItem('softline-guest-dialog');
        sessionStorage.removeItem('softline-guest');
        sessionStorage.removeItem('softline-token');
        token=''; session=state=role=result=dialogId=tab=undefined; break;
      case 'refresh': await loadState(); break;
      case 'role':
        if (value===role) break;
        {
          const next = await request('/api/state?role='+encodeURIComponent(value));
          resetWorkspace(); role=value; state=next; result=dialogId=tab=undefined; samplePage=0; sampleQuery=''; selectedOffer='';
        }
        break;
      case 'tab': tab=value; result=undefined; break;
      case 'cart-add': {
        const {cart}=clientContext();
        const offer=catalogOffer(value);
        if (!offer) break;
        if (cart.size>=20) throw Error('В корзине уже 20 позиций. Уберите одну, чтобы добавить товар.');
        cart.set(value,offer);
        break;
      }
      case 'cart-remove': clientContext().cart.delete(value); break;
      case 'workspace': training=value==='training'; tab=result=undefined; clearComposer(); break;
      case 'dialog': dialogId=value; result=undefined; clearComposer(); break;
      case 'open-dialog': clearComposer(); dialogId=value; chatOpen=true; chatPane=''; result=undefined; break;
      case 'new-dialog': clearComposer(); dialogId=''; chatOpen=true; chatPane=''; result=undefined; break;
      case 'chat-offer': {
        const offer=list(activeDialog()?.messages).flatMap(m=>list(m.offers)).find(o=>String(o.id)===value);
        if (offer) { chatOpen=false; tab='catalog'; selectedOffer=value; clientContext().viewed=offer; result={offers:[offer]}; }
        break;
      }
      case 'sample-page': samplePage=Number(value); break;
      case 'price-request': {
        await act('price_request',{offer_id:value,request_id:crypto.randomUUID()});
        chatOpen=true;
        break;
      }
      case 'order': await act('order_status',{order_id:value}); break;
      case 'download': await act('download',{document_id:value}); break;
      case 'availability': await act(action,{available:value==='true'}); break;
      case 'rate': await act('review',{dialog_id:dialogId,message_id:value,rating:control.dataset.rating}); break;
      case 'claim':
      case 'resolve': await act(action,{dialog_id:value}); break;
      case 'buyer_select': await act(action,{buyer_id:value}); resetWorkspace(); dialogId=result=undefined; break;
      case 'feed_import': await act(action,{day:Number(value)}); break;
      case 'template_delete':
        if (!window.confirm('Удалить черновик?')) break;
        await act(action,{template_id:value}); break;
      case 'template_publish':
      case 'template_rollback': await act(action,{template_id:value}); break;
    }
  },control,()=>{
    if (['quote','clear-quote','clear-file','chat-thread'].includes(action)) {
      app.querySelector('.composer-line textarea')?.focus();
      return;
    }
    if (action==='dialog') {
      const thread=app.querySelector('.staff-thread');
      if (thread) thread.scrollTop=thread.scrollHeight;
    }
    if (chatOpen && ['open-chat','starter','new-dialog','open-dialog'].includes(action)) {
      (app.querySelector('.widget textarea') ?? app.querySelector('.widget-head button'))?.focus();
      const thread=app.querySelector('.thread'); if (thread) thread.scrollTop=thread.scrollHeight;
    }
    if (action==='close-chat') app.querySelector('.launcher')?.focus();
    [...document.querySelectorAll('button[data-do]')].find(button=>button.dataset.do===action && button.dataset.value===value)?.focus();
  });
});
app.addEventListener('input',event=>{
  if (event.target.matches('[data-action="message"] textarea')) buyerDraft=event.target.value;
  if (event.target.matches('[data-action="reply"] textarea')) staffDraft=event.target.value;
});
app.addEventListener('change',event=>{
  if (event.target.matches('#jury-stand select[name="mode"]')) { buyerMode=event.target.value; render(); }
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
  if (event.key==='Escape' && chatOpen) app.querySelector('[data-do="close-chat"]')?.click();
});
// Update the open conversation without replacing a draft or stealing focus.
let checkingChat=false;
setInterval(async ()=>{
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
