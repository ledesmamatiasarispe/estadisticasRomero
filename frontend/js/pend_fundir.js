// pend_fundir.js — Pendiente de fundir por material: grafico, calendario (mes/anio),
// impresion y drill-down de OTs. Modulo compartido entre index.html y kiosk.html --
// UNICA fuente de esta funcionalidad, no se duplica en ninguno de los dos.
//
// Dependencias que el documento que lo carga debe definir ANTES de este script:
//   $id(id), api(path), fmt(n), fmtKg(n), fmtPesos(n)  -- helpers ya presentes en index.html y kiosk.html
//   Chart (Chart.js UMD, cargado por <script> aparte)
//   go(pagina, id)  -- navegacion opcional; index.html la tiene (clic en una OT del
//     detalle abre su ficha). kiosk.html no la define -- ver _pendFundirIrATrabajo,
//     que revisa que exista antes de llamarla y en kiosk simplemente no navega.

// 'piezas' | 'kg' | 'pesos' -- que unidad muestran las barras/tooltip/subtitulo.
// El nombre viejo (_pendFundirKgMode, booleano) se reemplaza por este modo de 3
// estados; pesos_pendientes viene de la misma resolucion de precio que usa
// /api/dashboard/pendiente_fundir/sin_precio (ver ese endpoint en main.py).
let _pendFundirModo = 'piezas';
let _pendFundirData   = null;
let _pendFundirIds    = null;   // {canvasId, subtitleId}
let _pendFundirCharts = {};     // {canvasId: Chart}
// Ultimo "meses" con el que se pidieron los datos -- lo usan el calendario y el
// drill-down de OTs para no depender de un S.dashMeses global que kiosk.html no
// tiene (index.html sigue siendo quien decide el valor, via el 3er arg de
// _renderPendienteFundirChart).
let _pendFundirMeses = 6;

function _pendFundirQS(extra) { return new URLSearchParams(extra).toString(); }

// Batiplane SIEMPRE esta incluido (no es un filtro) -- su volumen se resalta
// con un color mas oscuro dentro de cada barra/celda en vez de esconderse,
// asi ninguna OT pendiente de fundir queda fuera de la vista por defecto.
const _COLOR_PROGRAMADO           = '#42a5f5bb';
const _COLOR_PROGRAMADO_PRINT     = '#42a5f5';
const _COLOR_INICIAL              = '#9575cdbb';
const _COLOR_INICIAL_PRINT        = '#7e57c2';
const _COLOR_INICIAL_BATIPLANE       = '#5e35b1';
const _COLOR_INICIAL_BATIPLANE_PRINT = '#4527a0';
const _COLOR_PROGRAMADO_BATIPLANE       = '#00897b';
const _COLOR_PROGRAMADO_BATIPLANE_PRINT = '#00695c';
const _COLOR_FUNDIDO               = '#ffa726bb';
const _COLOR_FUNDIDO_PRINT         = '#ffa726';
const _COLOR_FUNDIDO_BATIPLANE       = '#e65100';
const _COLOR_FUNDIDO_BATIPLANE_PRINT = '#bf360c';

const _PEND_FUNDIR_LEYENDA_BATIPLANE_HTML = `
  <div style="display:flex;align-items:center;gap:10px;font-size:10px;color:var(--muted);margin:2px 0 8px">
    <span style="display:inline-flex;align-items:center;gap:4px">
      <span style="width:9px;height:9px;border-radius:2px;background:${_COLOR_INICIAL_BATIPLANE};display:inline-block;flex-shrink:0"></span>
      <span style="width:9px;height:9px;border-radius:2px;background:${_COLOR_PROGRAMADO_BATIPLANE};display:inline-block;flex-shrink:0"></span>
      <span style="width:9px;height:9px;border-radius:2px;background:${_COLOR_FUNDIDO_BATIPLANE};display:inline-block;flex-shrink:0"></span>
      Batiplane (color más oscuro, dentro de cada barra)
    </span>
  </div>`;

async function _renderPendienteFundirChart(canvasId, subtitleId, meses) {
  const canvas = $id(canvasId);
  if (!canvas) return;
  const m = meses != null ? meses : _pendFundirMeses;
  _pendFundirMeses = m;
  let data;
  try { data = await api('/api/dashboard/pendiente_fundir?' + _pendFundirQS({ meses: m })); }
  catch (e) { return; }
  _pendFundirData = data;
  _pendFundirIds  = { canvasId, subtitleId };
  // El HTML recien renderizado siempre arranca en modo grafico (el div del
  // calendario nace oculto) -- si el usuario navega afuera estando en modo
  // calendario, hay que resincronizar el flag con ese estado por defecto.
  _pendFundirCalMode = false;
  _drawPendienteFundirChart(canvasId, subtitleId, data);
}

const _PEND_FUNDIR_MODOS = ['piezas', 'kg', 'pesos'];

function _ciclarPendFundirModo() {
  const i = _PEND_FUNDIR_MODOS.indexOf(_pendFundirModo);
  _pendFundirModo = _PEND_FUNDIR_MODOS[(i + 1) % _PEND_FUNDIR_MODOS.length];
  if (_pendFundirData && _pendFundirIds) {
    _drawPendienteFundirChart(_pendFundirIds.canvasId, _pendFundirIds.subtitleId, _pendFundirData);
  }
}

// ── Modo calendario: mismo Programado/Fundido, pero agregado por fecha de
// entrega (sumando todos los materiales) en vez de por material -- responde
// "cuanto vence cada dia" en lugar de "cuanto pendiente hay por material".
let _pendFundirCalMode   = false;
let _pendFundirCalData   = null;  // {dias:[{fecha,programado,fundido}], sin_fecha}
let _pendFundirCalCursor = null;  // Date (dia 1 del mes mostrado)

function _pendFundirCalId(canvasId) { return canvasId.replace('chart-', 'cal-'); }
function _fechaISO(d) { return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); }
function _mesDeHoy() { const d = new Date(); d.setDate(1); d.setHours(0, 0, 0, 0); return d; }

async function _togglePendFundirCal() {
  if (!_pendFundirIds) return;
  _pendFundirCalMode = !_pendFundirCalMode;
  const { canvasId } = _pendFundirIds;
  const chartBox = $id(canvasId)?.closest('.chart-box');
  const calBox = $id(_pendFundirCalId(canvasId));
  const card = chartBox?.closest('.card');
  const kgBtn = card?.querySelector('#pend-fundir-kg-btn');
  const acciones = card?.querySelector('.pend-fundir-acciones');
  const calBtn = card?.querySelector('#pend-fundir-cal-btn');

  if (chartBox) chartBox.style.display = _pendFundirCalMode ? 'none' : '';
  if (calBox) calBox.style.display = _pendFundirCalMode ? '' : 'none';
  if (kgBtn) kgBtn.style.display = _pendFundirCalMode ? 'none' : '';
  if (acciones) acciones.style.display = _pendFundirCalMode ? 'none' : '';
  if (calBtn) calBtn.textContent = _pendFundirCalMode ? '📊 Volver al gráfico' : '📅 Modo calendario';

  if (!_pendFundirCalMode) return;
  if (!_pendFundirCalCursor) _pendFundirCalCursor = _mesDeHoy();
  if (!_pendFundirCalData) {
    if (calBox) calBox.innerHTML = '<div class="empty">Cargando&#8230;</div>';
    try { _pendFundirCalData = await api('/api/dashboard/pendiente_fundir/calendario?' + _pendFundirQS({ meses: _pendFundirMeses })); }
    catch (e) { if (calBox) calBox.innerHTML = '<div class="empty">No se pudo cargar</div>'; return; }
  }
  _renderPendFundirCalendario(canvasId);
}

let _pendFundirCalVista = 'mes'; // 'mes' | 'año'

function _pendFundirCalCambiarPeriodo(delta) {
  if (_pendFundirCalVista === 'año') _pendFundirCalCursor.setFullYear(_pendFundirCalCursor.getFullYear() + delta);
  else _pendFundirCalCursor.setMonth(_pendFundirCalCursor.getMonth() + delta);
  if (_pendFundirIds) _renderPendFundirCalendario(_pendFundirIds.canvasId);
}

function _pendFundirCalCambiarVista(vista) {
  _pendFundirCalVista = vista;
  if (_pendFundirIds) _renderPendFundirCalendario(_pendFundirIds.canvasId);
}

function _pendFundirCalIrAMes(mesIdx) {
  _pendFundirCalCursor.setMonth(mesIdx);
  _pendFundirCalVista = 'mes';
  if (_pendFundirIds) _renderPendFundirCalendario(_pendFundirIds.canvasId);
}

const _CAL_DIAS_SEMANA = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom'];
const _CAL_MESES = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio',
  'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre'];

function _pendFundirCalVistaBtnsHtml() {
  const activo = 'background:var(--accent-dim);color:var(--accent)';
  return `<span style="display:inline-flex;border:1px solid var(--border);border-radius:5px;overflow:hidden;margin-left:10px">
    <button class="page-btn" style="border:none;border-radius:0;padding:3px 10px;${_pendFundirCalVista === 'mes' ? activo : ''}" onclick="_pendFundirCalCambiarVista('mes')">Mes</button>
    <button class="page-btn" style="border:none;border-radius:0;padding:3px 10px;${_pendFundirCalVista === 'año' ? activo : ''}" onclick="_pendFundirCalCambiarVista('año')">A&ntilde;o</button>
  </span>`;
}

function _pendFundirCalImprimirBtnHtml() {
  return `<button class="page-btn" style="padding:3px 10px;margin-left:10px" onclick="_imprimirPendFundirCalendario()">&#128438; Imprimir</button>`;
}

function _pendFundirCalSinFechaHtml() {
  const sf = _pendFundirCalData.sin_fecha;
  const sfIni = sf && sf.inicial.ots;
  const sfProg = sf && sf.programado.ots;
  const sfFund = sf && sf.fundido.ots;
  if (!sfIni && !sfProg && !sfFund) return '';
  return `<div style="font-size:11px;color:var(--muted);margin-top:10px">
      &#9888; Sin fecha de entrega cargada:
      ${sfIni ? fmt(sf.inicial.ots) + ' OT iniciales (' + fmtKg(sf.inicial.kg_pendientes) + ')' : ''}
      ${sfIni && (sfProg || sfFund) ? ' &middot; ' : ''}
      ${sfProg ? fmt(sf.programado.ots) + ' OT programadas (' + fmtKg(sf.programado.kg_pendientes) + ')' : ''}
      ${sfProg && sfFund ? ' &middot; ' : ''}
      ${sfFund ? fmt(sf.fundido.ots) + ' OT fundidas (' + fmtKg(sf.fundido.kg_pendientes) + ')' : ''}
    </div>`;
}

function _renderPendFundirCalendario(canvasId) {
  const el = $id(_pendFundirCalId(canvasId));
  if (!el || !_pendFundirCalData) return;
  if (_pendFundirCalVista === 'año') _renderPendFundirCalendarioAnual(el);
  else _renderPendFundirCalendarioMensual(el);
}

// Un segmento (programado/fundido, de un dia o ya sumado por semana/mes) en
// badges: resto + Batiplane en color mas oscuro cuando corresponde, uno solo
// si no hay Batiplane ese periodo -- misma regla en dia, semana y mes.
// seg: {ots, kg, batOts, batKg}
function _pendFundirCalBadgeHtml(seg, clase, titulo) {
  if (!seg || !seg.ots) return '';
  if (!seg.batOts) return `<div class="cal-badge ${clase}" title="${titulo}">${fmt(seg.ots)} OT &middot; ${fmtKg(seg.kg)}</div>`;
  const restoOts = seg.ots - seg.batOts, restoKg = seg.kg - seg.batKg;
  const restoHtml = restoOts > 0
    ? `<div class="cal-badge ${clase}" title="${titulo}">${fmt(restoOts)} OT &middot; ${fmtKg(restoKg)}</div>` : '';
  return restoHtml
    + `<div class="cal-badge ${clase}-bat" title="${titulo} (Batiplane)">${fmt(seg.batOts)} OT &middot; ${fmtKg(seg.batKg)}</div>`;
}
// Normaliza la forma cruda que trae la API ({ots, kg_pendientes, batiplane:{ots,kg_pendientes}})
// a la forma acumulable {ots, kg, batOts, batKg} que usan las sumas por semana/mes.
function _pendFundirCalNormSeg(seg) {
  return {
    ots: (seg && seg.ots) || 0, kg: (seg && seg.kg_pendientes) || 0,
    batOts: (seg && seg.batiplane && seg.batiplane.ots) || 0,
    batKg: (seg && seg.batiplane && seg.batiplane.kg_pendientes) || 0,
  };
}

function _renderPendFundirCalendarioMensual(el) {
  const cursor = _pendFundirCalCursor;
  const año = cursor.getFullYear(), mes = cursor.getMonth();
  const porFecha = new Map((_pendFundirCalData.dias || []).map(d => [d.fecha, d]));

  const primerDia = new Date(año, mes, 1);
  const ultimoDia = new Date(año, mes + 1, 0);
  const offsetInicio = (primerDia.getDay() + 6) % 7; // lunes = 0
  const totalDias = ultimoDia.getDate();
  const hoyStr = _fechaISO(new Date());

  let celdas = '';
  for (let i = 0; i < offsetInicio; i++) celdas += '<div class="cal-celda cal-vacia"></div>';
  for (let dia = 1; dia <= totalDias; dia++) {
    const fechaStr = _fechaISO(new Date(año, mes, dia));
    const d = porFecha.get(fechaStr);
    const esHoy = fechaStr === hoyStr;
    const vencida = fechaStr < hoyStr && d && (d.inicial.ots > 0 || d.programado.ots > 0);
    const tieneDatos = d && (d.inicial.ots || d.programado.ots || d.fundido.ots);
    const clickAttr = tieneDatos ? ` style="cursor:pointer" onclick="_pendFundirCalAbrirDia('${fechaStr}')"` : '';
    celdas += `<div class="cal-celda${esHoy ? ' cal-hoy' : ''}${vencida ? ' cal-vencida' : ''}"${clickAttr}>
      <div class="cal-num">${dia}</div>
      ${d ? _pendFundirCalBadgeHtml(_pendFundirCalNormSeg(d.inicial), 'cal-ini', 'Inicial') : ''}
      ${d ? _pendFundirCalBadgeHtml(_pendFundirCalNormSeg(d.programado), 'cal-prog', 'Programado') : ''}
      ${d ? _pendFundirCalBadgeHtml(_pendFundirCalNormSeg(d.fundido), 'cal-fund', 'Fundido') : ''}
    </div>`;
  }
  const restante = (7 - ((offsetInicio + totalDias) % 7)) % 7;
  for (let i = 0; i < restante; i++) celdas += '<div class="cal-celda cal-vacia"></div>';

  el.innerHTML = `
    <div style="display:flex;align-items:center;justify-content:center;gap:14px;margin-bottom:10px">
      <button class="page-btn" style="padding:3px 10px" onclick="_pendFundirCalCambiarPeriodo(-1)">&#8249;</button>
      <span style="font-size:13px;font-weight:600;min-width:150px;text-align:center">${_CAL_MESES[mes]} ${año}</span>
      <button class="page-btn" style="padding:3px 10px" onclick="_pendFundirCalCambiarPeriodo(1)">&#8250;</button>
      ${_pendFundirCalVistaBtnsHtml()}
      ${_pendFundirCalImprimirBtnHtml()}
    </div>
    <div class="cal-grid cal-header">${_CAL_DIAS_SEMANA.map(d => `<div class="cal-dow">${d}</div>`).join('')}</div>
    <div class="cal-grid">${celdas}</div>
    ${_pendFundirCalSinFechaHtml()}`;
}

// Semana-del-mes de un dia: bloques fijos de 7 dias (1-7, 8-14, 15-21, 22-fin)
// sin importar que dia de la semana arranca el mes -- 4 semanas siempre,
// mismo numero de fila en cualquier mes, para que "semana 2" siempre sea
// "8-14" y no dependa de si el mes empezo lunes o jueves.
function _semanaDelMes(dia) {
  return Math.min(4, Math.ceil(dia / 7));
}

// Agrupa _pendFundirCalData.dias en {totalDias, semanas: [4 x {diaMin,diaMax,
// vencido,programado,fundido}], vencido} por mes, para la vista anual
// (pantalla e impresion, mismo calculo para las dos). Las 4 semanas siempre
// estan presentes -- rango fijo aunque no tengan ninguna OT esa semana.
function _pendFundirCalAgruparAnual(año) {
  const hoyStr = _fechaISO(new Date());
  const meses = Array.from({ length: 12 }, (_, i) => {
    const totalDias = new Date(año, i + 1, 0).getDate();
    const rangos = [[1, 7], [8, 14], [15, 21], [22, totalDias]];
    return {
      totalDias,
      semanas: rangos.map(([diaMin, diaMax]) => ({
        diaMin, diaMax, vencido: false,
        inicial: { ots: 0, kg: 0, batOts: 0, batKg: 0 }, programado: { ots: 0, kg: 0, batOts: 0, batKg: 0 }, fundido: { ots: 0, kg: 0, batOts: 0, batKg: 0 },
      })),
      vencido: false,
    };
  });
  (_pendFundirCalData.dias || []).forEach((d) => {
    const [y, m, dia] = d.fecha.split('-').map(Number);
    if (y !== año) return;
    const mesInfo = meses[m - 1];
    const sem = mesInfo.semanas[_semanaDelMes(dia) - 1];
    const acumular = (seg, raw) => {
      seg.ots += raw.ots || 0;
      seg.kg  += raw.kg_pendientes || 0;
      seg.batOts += (raw.batiplane && raw.batiplane.ots) || 0;
      seg.batKg  += (raw.batiplane && raw.batiplane.kg_pendientes) || 0;
    };
    acumular(sem.inicial, d.inicial);
    acumular(sem.programado, d.programado);
    acumular(sem.fundido, d.fundido);
    if (d.fecha < hoyStr && (d.inicial.ots > 0 || d.programado.ots > 0)) { sem.vencido = true; mesInfo.vencido = true; }
  });
  return meses;
}

function _renderPendFundirCalendarioAnual(el) {
  const año = _pendFundirCalCursor.getFullYear();
  const meses = _pendFundirCalAgruparAnual(año);

  const celdas = _CAL_MESES.map((nombre, i) => {
    const mesInfo = meses[i];
    const tieneDatos = mesInfo.semanas.some(sem => sem.inicial.ots || sem.programado.ots || sem.fundido.ots);
    const filas = mesInfo.semanas.map((sem) => {
      const rango = sem.diaMin === sem.diaMax ? `${sem.diaMin}` : `${sem.diaMin}-${sem.diaMax}`;
      return `<tr class="${sem.vencido ? 'cal-semana-vencida' : ''}">
        <td class="cal-mes-td-sem">${rango}</td>
        <td><div class="cal-mes-td-badges">${_pendFundirCalBadgeHtml(sem.inicial, 'cal-ini', 'Inicial')}</div></td>
        <td><div class="cal-mes-td-badges">${_pendFundirCalBadgeHtml(sem.programado, 'cal-prog', 'Programado')}</div></td>
        <td><div class="cal-mes-td-badges">${_pendFundirCalBadgeHtml(sem.fundido, 'cal-fund', 'Fundido')}</div></td>
      </tr>`;
    }).join('');
    const tabla = tieneDatos ? `<table class="cal-mes-tabla">
      <thead><tr><th>Sem.</th><th>Inicial</th><th>Programado</th><th>Fundido</th></tr></thead>
      <tbody>${filas}</tbody>
    </table>` : '<div class="cal-mes-vacio">&mdash;</div>';
    return `<div class="cal-mes-celda${mesInfo.vencido ? ' cal-vencida' : ''}" onclick="_pendFundirCalIrAMes(${i})">
      <div class="cal-mes-nombre">${nombre}</div>
      ${tabla}
    </div>`;
  }).join('');

  el.innerHTML = `
    <div style="display:flex;align-items:center;justify-content:center;gap:14px;margin-bottom:10px">
      <button class="page-btn" style="padding:3px 10px" onclick="_pendFundirCalCambiarPeriodo(-1)">&#8249;</button>
      <span style="font-size:13px;font-weight:600;min-width:150px;text-align:center">${año}</span>
      <button class="page-btn" style="padding:3px 10px" onclick="_pendFundirCalCambiarPeriodo(1)">&#8250;</button>
      ${_pendFundirCalVistaBtnsHtml()}
      ${_pendFundirCalImprimirBtnHtml()}
    </div>
    <div class="cal-anual-grid">${celdas}</div>
    ${_pendFundirCalSinFechaHtml()}`;
}

function _pendFundirCalAbrirDia(fechaStr) {
  const [y, m, d] = fechaStr.split('-');
  _abrirPendFundirDetalle(`Fecha de entrega — ${d}/${m}/${y}`, { fecha: fechaStr });
}

// Un segmento como pill solida (fondo de color, texto blanco) para la hoja
// impresa -- los badges semitransparentes de pantalla se ven lavados sobre
// papel blanco. Mismo split resto/Batiplane que en pantalla.
function _pendFundirImpPill(seg, colorResto, colorBat) {
  if (!seg || !seg.ots) return '';
  const pill = (n, kg, color) => `<span style="display:inline-block;background:${color};color:#fff;border-radius:3px;
    padding:1px 5px;margin:1px 3px 1px 0;font-size:10px;font-weight:700;white-space:nowrap">${fmt(n)} OT &middot; ${fmtKg(kg)}</span>`;
  const restoOts = seg.ots - seg.batOts, restoKg = seg.kg - seg.batKg;
  return (restoOts > 0 ? pill(restoOts, restoKg, colorResto) : '')
    + (seg.batOts > 0 ? pill(seg.batOts, seg.batKg, colorBat) : '');
}

// Vista anual impresa: una tabla Sem./Programado/Fundido por mes, igual que
// en pantalla -- los meses sin ninguna semana con datos no se imprimen.
function _imprimirCalendarioAnualHtml(año) {
  const meses = _pendFundirCalAgruparAnual(año);
  return _CAL_MESES.map((nombre, i) => {
    const mesInfo = meses[i];
    const tieneDatos = mesInfo.semanas.some(sem => sem.programado.ots || sem.fundido.ots);
    if (!tieneDatos) return '';
    const filas = mesInfo.semanas.map((sem) => `<tr>
        <td style="font-weight:${sem.vencido ? '700' : '400'};color:${sem.vencido ? '#c62828' : '#333'}">${sem.diaMin === sem.diaMax ? sem.diaMin : sem.diaMin + '-' + sem.diaMax}${sem.vencido ? ' &#9888;' : ''}</td>
        <td>${_pendFundirImpPill(sem.programado, _COLOR_PROGRAMADO_PRINT, _COLOR_PROGRAMADO_BATIPLANE_PRINT)}</td>
        <td>${_pendFundirImpPill(sem.fundido, _COLOR_FUNDIDO_PRINT, _COLOR_FUNDIDO_BATIPLANE_PRINT)}</td>
      </tr>`).join('');
    return `<div class="p-mes">
      <h3>${nombre}${mesInfo.vencido ? ' <span style="color:#c62828;font-size:11px">&#9888; vencido</span>' : ''}</h3>
      <table><thead><tr><th style="width:50px">Sem.</th><th>Programado</th><th>Fundido</th></tr></thead>
      <tbody>${filas}</tbody></table>
    </div>`;
  }).filter(Boolean).join('');
}

// Vista mensual impresa: la misma grilla de 7 columnas que en pantalla.
function _imprimirCalendarioMensualHtml(año, mes) {
  const porFecha = new Map((_pendFundirCalData.dias || []).map(d => [d.fecha, d]));
  const primerDia = new Date(año, mes, 1);
  const ultimoDia = new Date(año, mes + 1, 0);
  const offsetInicio = (primerDia.getDay() + 6) % 7;
  const totalDias = ultimoDia.getDate();
  const hoyStr = _fechaISO(new Date());

  let celdas = '';
  for (let i = 0; i < offsetInicio; i++) celdas += '<td class="p-cal-vacia"></td>';
  for (let dia = 1; dia <= totalDias; dia++) {
    const fechaStr = _fechaISO(new Date(año, mes, dia));
    const d = porFecha.get(fechaStr);
    const vencida = fechaStr < hoyStr && d && d.programado.ots > 0;
    celdas += `<td class="p-cal-celda">
      <div style="font-weight:700;color:${vencida ? '#c62828' : '#333'};font-size:11px">${dia}${vencida ? ' &#9888;' : ''}</div>
      ${d ? _pendFundirImpPill(_pendFundirCalNormSeg(d.programado), _COLOR_PROGRAMADO_PRINT, _COLOR_PROGRAMADO_BATIPLANE_PRINT) : ''}
      ${d ? '<br>' : ''}
      ${d ? _pendFundirImpPill(_pendFundirCalNormSeg(d.fundido), _COLOR_FUNDIDO_PRINT, _COLOR_FUNDIDO_BATIPLANE_PRINT) : ''}
    </td>`;
    if ((offsetInicio + dia) % 7 === 0) celdas += '</tr><tr>';
  }
  const restante = (7 - ((offsetInicio + totalDias) % 7)) % 7;
  for (let i = 0; i < restante; i++) celdas += '<td class="p-cal-vacia"></td>';

  return `<table class="p-cal-grid">
    <thead><tr>${_CAL_DIAS_SEMANA.map(d => `<th>${d}</th>`).join('')}</tr></thead>
    <tbody><tr>${celdas}</tr></tbody>
  </table>`;
}

function _imprimirPendFundirCalendario() {
  if (!_pendFundirCalData) { alert('Esperá a que termine de cargar el calendario.'); return; }
  const now = new Date().toLocaleString('es-AR');
  const año = _pendFundirCalCursor.getFullYear();
  const esAnual = _pendFundirCalVista === 'año';
  const titulo = esAnual ? `Calendario ${año}` : `Calendario ${_CAL_MESES[_pendFundirCalCursor.getMonth()]} ${año}`;
  const cuerpo = esAnual
    ? `<div class="p-anual">${_imprimirCalendarioAnualHtml(año)}</div>`
    : _imprimirCalendarioMensualHtml(año, _pendFundirCalCursor.getMonth());

  const w = window.open('', '_blank', 'width=1000,height=750,scrollbars=yes');
  if (!w) { alert('Permitir ventanas emergentes para usar esta función.'); return; }
  w.document.open();
  w.document.write(`<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<title>${titulo}</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,-apple-system,sans-serif;color:#111;background:#fff;padding:24px 28px}
h1{font-size:17px;font-weight:700;color:#333;margin-bottom:3px}
.period{font-size:11px;color:#888;margin-bottom:16px}
.print-date{font-size:16px;color:#000;font-weight:700}
.p-anual{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
.p-mes{border:1px solid #ddd;border-radius:6px;padding:8px;break-inside:avoid}
.p-mes h3{font-size:13px;margin-bottom:6px}
.p-mes table, .p-cal-grid{width:100%;border-collapse:collapse}
.p-mes th, .p-cal-grid th{text-align:left;font-size:9px;text-transform:uppercase;letter-spacing:.03em;color:#888;
  border-bottom:1px solid #ccc;padding:2px 4px}
.p-mes td{padding:3px 4px;border-bottom:1px solid #eee;vertical-align:top;font-size:11px}
.p-cal-grid th{text-align:center;padding:4px}
.p-cal-grid td{border:1px solid #eee;vertical-align:top;padding:4px;height:70px;width:14.28%}
.p-cal-vacia{background:#fafafa}
@media print{body{padding:0}@page{margin:12mm;size:A4 ${esAnual ? 'landscape' : 'portrait'}}}
</style></head><body>
<h1>${titulo}</h1>
<div class="period"><span class="print-date">Impreso: ${now}</span></div>
${cuerpo}
</body></html>`);
  w.document.close();
  setTimeout(() => w.print(), 500);
}

// ── Modal de detalle: lista de OTs detras de una barra (codigo+estado) o de
// un dia de calendario (fecha, sin estado -- trae Programadas y Fundidas
// juntas, cada fila con la suya) -- mismo endpoint para los dos casos.
let _pendFundirDetalleRows = [];
let _pendFundirDetalleTitulo = 'OTs';

async function _abrirPendFundirDetalle(titulo, filtros) {
  const modal = $id('pend-fundir-detalle-modal');
  const body = $id('pend-fundir-detalle-modal-body');
  const tituloEl = $id('pend-fundir-detalle-titulo');
  _pendFundirDetalleTitulo = titulo || 'OTs';
  if (tituloEl) tituloEl.textContent = titulo;
  if (modal) modal.classList.add('open');
  if (body) body.innerHTML = '<div class="loading">Cargando...</div>';

  let rows;
  try { rows = await api('/api/dashboard/pendiente_fundir/detalle?' + _pendFundirQS({ meses: _pendFundirMeses, ...filtros })); }
  catch (e) { if (body) body.innerHTML = '<div class="empty">No se pudo cargar</div>'; return; }

  _pendFundirDetalleRows = rows;
  if (!body) return;
  if (!rows.length) { body.innerHTML = '<div class="empty">Sin OTs</div>'; return; }

  const totalPz = rows.reduce((s, r) => s + (r.pendientes || 0), 0);
  const totalKg = rows.reduce((s, r) => s + (r.kg_pendientes || 0), 0);
  const totalPesos = rows.reduce((s, r) => s + (r.pesos_pendientes || 0), 0);
  const hayVarios = new Set(rows.map(r => r.estado)).size > 1;

  body.innerHTML = `
    <div style="font-size:12px;color:var(--muted);margin-bottom:10px">
      ${fmt(rows.length)} OT${rows.length !== 1 ? 's' : ''} &middot; ${fmt(totalPz)} piezas &middot; ${fmtKg(totalKg)} &middot; ${fmtPesos(totalPesos)}
    </div>
    <div class="tbl-wrap">
      <table>
        <thead><tr>
          <th>OT</th>${hayVarios ? '<th>Estado</th>' : ''}<th>Pieza</th><th>Material</th><th>Cliente</th>
          <th style="text-align:right">Pend.</th><th style="text-align:right">Kg</th><th style="text-align:right">$</th><th>Entrega</th>
        </tr></thead>
        <tbody>${rows.map(r => `
          <tr class="tr-link" onclick="closePendFundirDetalleModal();_pendFundirIrATrabajo(${r.ot_id})">
            <td><code style="color:var(--accent)">${String(r.ot_id).padStart(6, '0')}</code></td>
            ${hayVarios ? `<td>${r.estado_label || '—'}</td>` : ''}
            <td>${r.nombrepieza || '—'}</td>
            <td>${r.material || '—'}</td>
            <td>${r.cliente_nombre || '—'}${r.codigo_cliente === 'BA3' ? ' <span style="color:#7cc0f8;font-size:10px;font-weight:700">&#9679; Batiplane</span>' : ''}<div style="font-size:11px;color:var(--muted)">${r.codigo_cliente || ''}</div></td>
            <td style="text-align:right">${fmt(r.pendientes)}</td>
            <td style="text-align:right">${r.kg_pendientes != null ? fmtKg(r.kg_pendientes) : '—'}</td>
            <td style="text-align:right">${r.pesos_pendientes != null ? fmtPesos(r.pesos_pendientes) : '—'}</td>
            <td style="font-size:11px;color:var(--muted)">${r.fechaprevista ? r.fechaprevista.slice(0, 10) : '—'}</td>
          </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
}

function _imprimirPendFundirDetalle() {
  const rows = _pendFundirDetalleRows;
  if (!rows.length) return;
  const now = new Date().toLocaleString('es-AR');
  const totalPz = rows.reduce((s, r) => s + (r.pendientes || 0), 0);
  const totalKg = rows.reduce((s, r) => s + (r.kg_pendientes || 0), 0);
  const totalPesos = rows.reduce((s, r) => s + (r.pesos_pendientes || 0), 0);
  const cuerpo = rows.map(r => `
    <tr>
      <td><strong>${String(r.ot_id).padStart(6, '0')}</strong></td>
      <td>${r.estado_label || r.estado || '—'}</td>
      <td>${r.nombrepieza || '—'}<div class="sub">${r.codigo_pieza || ''}</div></td>
      <td>${r.material || '—'}</td>
      <td>${r.cliente_nombre || '—'}<div class="sub">${r.codigo_cliente || ''}</div></td>
      <td class="num">${fmt(r.pendientes)}</td>
      <td class="num">${r.kg_pendientes != null ? fmtKg(r.kg_pendientes) : '—'}</td>
      <td class="num">${r.pesos_pendientes != null ? fmtPesos(r.pesos_pendientes) : '—'}</td>
      <td>${r.fechaprevista ? r.fechaprevista.slice(0, 10) : '—'}</td>
      <td class="obs"></td>
    </tr>`).join('');

  const w = window.open('', '_blank', 'width=1100,height=760,scrollbars=yes');
  if (!w) { alert('Permití ventanas emergentes para imprimir.'); return; }
  w.document.open();
  w.document.write(`<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
<title>${_pendFundirDetalleTitulo}</title><style>
*{box-sizing:border-box}body{font-family:Arial,sans-serif;color:#111;padding:18px;font-size:10px}
h1{font-size:17px;margin:0 0 4px}.resumen{color:#555;margin-bottom:14px}
table{width:100%;border-collapse:collapse}th{background:#eee;text-align:left;font-size:9px;text-transform:uppercase}
th,td{border:1px solid #ccc;padding:5px;vertical-align:top}.num{text-align:right;white-space:nowrap}.sub{font-size:8px;color:#666;margin-top:2px}.obs{min-width:90px}
@media print{body{padding:0}@page{size:A4 landscape;margin:10mm}}
</style></head><body>
<h1>${_pendFundirDetalleTitulo}</h1>
<div class="resumen">Impreso: ${now} · ${fmt(rows.length)} OTs · ${fmt(totalPz)} piezas · ${fmtKg(totalKg)} · ${fmtPesos(totalPesos)}</div>
<table><thead><tr><th>OT</th><th>Estado</th><th>Pieza</th><th>Material</th><th>Cliente</th><th>Pend.</th><th>Kg</th><th>$</th><th>Vencimiento</th><th>Observaciones</th></tr></thead>
<tbody>${cuerpo}</tbody></table></body></html>`);
  w.document.close();
  setTimeout(() => w.print(), 500);
}

function closePendFundirDetalleModal() {
  const modal = $id('pend-fundir-detalle-modal');
  if (modal) modal.classList.remove('open');
}

// index.html tiene go(pagina, id) para navegar a la ficha de la OT; kiosk.html
// es una pantalla de solo lectura sin router y no la define -- ahi el click
// en una fila del detalle simplemente cierra el modal, sin intentar navegar.
function _pendFundirIrATrabajo(otId) {
  if (typeof go === 'function') go('trabajo', otId);
}

// "Nice number" para el paso mayor de la regla: ~6 marcas mayores prolijas
// (1/2/5 × potencia de 10) sobre el maximo actual, con 5 subdivisiones menores
// por cada una -- asi el paso depende del total en vez de ser fijo.
function _reglaPasos(maxVal) {
  if (!maxVal || maxVal <= 0) return { major: 1, minor: 0.2, subdiv: 5 };
  const rough = maxVal / 6;
  const mag = Math.pow(10, Math.floor(Math.log10(rough)));
  const norm = rough / mag;
  const niceNorm = norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10;
  const major = niceNorm * mag;
  const subdiv = 5;
  return { major, minor: major / subdiv, subdiv };
}

// Dibuja las marcas de la regla ENCIMA de cada barra (no solo en el fondo del
// eje, que las barras tapan) -- una linea vertical por cada paso menor dentro
// del relleno de la barra, mas oscura/gruesa en cada paso mayor.
// Path de rectangulo redondeado para recortar el tramo de Batiplane al mismo
// contorno que Chart.js usa para dibujar la barra (borderRadius) -- si no,
// cuando el tramo llega hasta la punta de la barra asoma en angulo recto.
function _pathRectRedondeado(ctx, x1, y1, x2, y2, r) {
  const rr = Math.max(0, Math.min(r, Math.abs(x2 - x1) / 2, Math.abs(y2 - y1) / 2));
  ctx.beginPath();
  if (ctx.roundRect) {
    ctx.roundRect(Math.min(x1, x2), y1, Math.abs(x2 - x1), y2 - y1, rr);
  } else {
    ctx.moveTo(x1 + rr, y1);
    ctx.arcTo(x2, y1, x2, y2, rr);
    ctx.arcTo(x2, y2, x1, y2, rr);
    ctx.arcTo(x1, y2, x1, y1, rr);
    ctx.arcTo(x1, y1, x2, y1, rr);
    ctx.closePath();
  }
}

const _reglaBarraPlugin = {
  id: 'reglaBarra',
  afterDatasetsDraw(chart, args, opts) {
    if (!opts || !opts.minor) return;
    const { minor, subdiv, etiquetas, batiplaneValores, batiplaneColores, print } = opts;
    // etiquetas[datasetIndex][i]: una barra por serie (agrupadas, no apiladas)
    // -- cada una con su propia regla y su propio total, no uno combinado.
    const datasets = chart.data.datasets;
    const xScale = chart.scales.x;
    const x0 = xScale.getPixelForValue(0);
    const ctx = chart.ctx;
    ctx.save();
    datasets.forEach((ds, dsIdx) => {
      if (!chart.isDatasetVisible(dsIdx)) return;
      const meta = chart.getDatasetMeta(dsIdx);
      if (!meta || !meta.data.length) return;
      meta.data.forEach((bar, i) => {
        const v = ds.data[i] || 0;
        if (v <= 0) return;
        const top = bar.y - bar.height / 2 + 1;
        const bottom = bar.y + bar.height / 2 - 1;
        const xEnd = bar.x;

        // Porcion de Batiplane -- se resalta en un color mas oscuro sobre la
        // punta de la barra (el resto ya quedo pintado por Chart.js), en vez
        // de excluirla: nunca esconde una OT pendiente de fundir.
        const vBat = batiplaneValores && batiplaneValores[dsIdx] && batiplaneValores[dsIdx][i];
        if (vBat > 0) {
          const xBatIni = xScale.getPixelForValue(Math.max(0, v - vBat));
          const yTop = bar.y - bar.height / 2, yBottom = bar.y + bar.height / 2;
          ctx.save();
          _pathRectRedondeado(ctx, x0, yTop, xEnd, yBottom, bar.options ? (bar.options.borderRadius || 0) : 4);
          ctx.clip();
          ctx.fillStyle = batiplaneColores[dsIdx];
          ctx.fillRect(xBatIni, yTop, xEnd - xBatIni, yBottom - yTop);
          ctx.restore();
        }

        for (let n = 1; n * minor < v; n++) {
          const tickVal = n * minor;
          const px = xScale.getPixelForValue(tickVal);
          if (px <= x0 || px >= xEnd) continue;
          const esMayor = Math.round(tickVal / minor) % subdiv === 0;
          ctx.strokeStyle = esMayor ? 'rgba(13,17,23,.55)' : 'rgba(13,17,23,.22)';
          ctx.lineWidth = esMayor ? 1.5 : 1;
          ctx.beginPath();
          ctx.moveTo(px, top);
          ctx.lineTo(px, bottom);
          ctx.stroke();
        }
        // Total al final de cada barra -- en la imagen impresa no hay tooltip
        // para ver el valor exacto al pasar el mouse, asi que queda a la vista.
        const etiqueta = etiquetas && etiquetas[dsIdx] && etiquetas[dsIdx][i];
        if (etiqueta != null) {
          ctx.fillStyle = print ? '#000' : '#c9d1d9';
          ctx.font = (print ? '700 15px' : '700 11px') + ' system-ui, -apple-system, sans-serif';
          ctx.textAlign = 'left';
          ctx.textBaseline = 'middle';
          ctx.fillText(String(etiqueta), xEnd + 6, bar.y);
        }
      });
    });
    ctx.restore();
  },
};
Chart.register(_reglaBarraPlugin);

// Orden fijo pedido para agrupar materiales visualmente (no alfabetico ni por
// cantidad) -- cada sub-lista es un grupo con su propia llave y su kg total.
// Los materiales que no figuran acá van al final, en su orden normal.
const _PEND_FUNDIR_GRUPOS = [
  ['5', '1', '3', '7'],
  ['2', '1/2'],
  ['10', '12', '13', '8'],
];

function _ordenarPorGrupos(data) {
  const porCodigo = new Map(data.map(d => [d.codigo, d]));
  const usados = new Set();
  const ordenada = [];
  const grupos = [];
  for (const codigos of _PEND_FUNDIR_GRUPOS) {
    const startIdx = ordenada.length;
    // kg y pesos por serie -- el plugin suma solo lo que este visible segun la
    // leyenda, asi apagar "Fundido" tambien saca su parte del total de la llave.
    const tot = {
      kgInicial: 0, kgProgramado: 0, kgFundido: 0,
      kgInicialBatiplane: 0, kgProgramadoBatiplane: 0, kgFundidoBatiplane: 0,
      pesosInicial: 0, pesosProgramado: 0, pesosFundido: 0,
      pesosInicialBatiplane: 0, pesosProgramadoBatiplane: 0, pesosFundidoBatiplane: 0,
    };
    let huboAlguno = false;
    for (const cod of codigos) {
      const d = porCodigo.get(cod);
      if (!d) continue;
      ordenada.push(d);
      usados.add(cod);
      tot.kgInicial += d.inicial.kg_pendientes || 0;
      tot.kgProgramado += d.programado.kg_pendientes || 0;
      tot.kgFundido += d.fundido.kg_pendientes || 0;
      tot.kgInicialBatiplane += (d.inicial.batiplane && d.inicial.batiplane.kg_pendientes) || 0;
      tot.kgProgramadoBatiplane += (d.programado.batiplane && d.programado.batiplane.kg_pendientes) || 0;
      tot.kgFundidoBatiplane += (d.fundido.batiplane && d.fundido.batiplane.kg_pendientes) || 0;
      tot.pesosInicial += d.inicial.pesos_pendientes || 0;
      tot.pesosProgramado += d.programado.pesos_pendientes || 0;
      tot.pesosFundido += d.fundido.pesos_pendientes || 0;
      tot.pesosInicialBatiplane += (d.inicial.batiplane && d.inicial.batiplane.pesos_pendientes) || 0;
      tot.pesosProgramadoBatiplane += (d.programado.batiplane && d.programado.batiplane.pesos_pendientes) || 0;
      tot.pesosFundidoBatiplane += (d.fundido.batiplane && d.fundido.batiplane.pesos_pendientes) || 0;
      huboAlguno = true;
    }
    if (huboAlguno) grupos.push({ startIdx, endIdx: ordenada.length - 1, ...tot });
  }
  for (const d of data) { if (!usados.has(d.codigo)) ordenada.push(d); }
  return { ordenada, grupos };
}

// Llave "{" a la izquierda de las etiquetas del eje, con el kg total del
// grupo -- se dibuja en el margen izquierdo reservado via layout.padding.
function _dibujarLlave(ctx, xLabel, yTop, yBottom, prof) {
  const yMid = (yTop + yBottom) / 2;
  const xSpine = xLabel - prof;
  const xTip = xSpine - prof;
  ctx.beginPath();
  ctx.moveTo(xLabel, yTop);
  ctx.bezierCurveTo(xSpine, yTop, xSpine, yMid - prof, xTip, yMid);
  ctx.bezierCurveTo(xSpine, yMid + prof, xSpine, yBottom, xLabel, yBottom);
  ctx.stroke();
  return xTip;
}

const _grupoLlavePlugin = {
  id: 'grupoLlave',
  afterDraw(chart, args, opts) {
    if (!opts || !opts.grupos || !opts.grupos.length) return;
    const meta0 = chart.getDatasetMeta(0);
    const meta1 = chart.getDatasetMeta(1);
    const meta2 = chart.getDatasetMeta(2);
    if (!meta0 || !meta0.data.length) return;
    const ctx = chart.ctx;
    // chart.scales.y.left es el borde del margen reservado por layout.padding.left
    // -- chart.chartArea.left queda DESPUES de las etiquetas del eje (5, 1, 3...),
    // dibujar ahi las tapa en vez de quedar en el margen libre.
    const xLabel = chart.scales.y.left - 4;
    const prof = opts.print ? 10 : 8;
    // Togglear una serie en la leyenda saca su parte del kg de la llave --
    // el total mostrado siempre coincide con lo que las barras estan mostrando.
    const inicialVisible = chart.isDatasetVisible(0);
    const progVisible = chart.isDatasetVisible(1);
    const fundVisible = chart.isDatasetVisible(2);
    ctx.save();
    ctx.strokeStyle = opts.print ? '#333' : '#8b929e';
    ctx.lineWidth = 1.5;
    const fontSize = opts.print ? 11 : 9;
    ctx.font = (opts.print ? '700 ' : '700 ') + fontSize + 'px system-ui, -apple-system, sans-serif';
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    const colorProg    = opts.print ? _COLOR_PROGRAMADO_PRINT : '#7cc0f8';
    const colorFund     = opts.print ? _COLOR_FUNDIDO_PRINT : '#ffc477';
    const colorInicial  = opts.print ? _COLOR_INICIAL_PRINT : '#b39ddb';
    const colorProgBat = opts.print ? _COLOR_PROGRAMADO_BATIPLANE_PRINT : _COLOR_PROGRAMADO_BATIPLANE;
    const colorFundBat  = opts.print ? _COLOR_FUNDIDO_BATIPLANE_PRINT : _COLOR_FUNDIDO_BATIPLANE;
    // La llave siempre muestra $ en modo pesos, kg en los otros dos modos --
    // es una anotacion secundaria (no la metrica principal de las barras), y
    // kg es el dato mas accionable salvo cuando el usuario pidio ver plata.
    const f = opts.modo === 'pesos' ? fmtPesos : fmtKg;
    const pfx = opts.modo === 'pesos' ? 'pesos' : 'kg';
    opts.grupos.forEach((g) => {
      const barTop = meta0.data[g.startIdx];
      const barBottomEl = (meta2 && meta2.data[g.endIdx]) || (meta1 && meta1.data[g.endIdx]) || meta0.data[g.endIdx];
      if (!barTop || !barBottomEl) return;
      const yTop = barTop.y - barTop.height / 2 - 3;
      const yBottom = barBottomEl.y + barBottomEl.height / 2 + 3;
      const xTip = _dibujarLlave(ctx, xLabel, yTop, yBottom, prof);
      // Total de Programado y de Fundido por separado, y dentro de cada uno
      // Batiplane tambien aparte (resto + Batiplane, se omite el renglon que
      // de cero) -- mismo criterio que las barras y el calendario, nunca un
      // numero mezclado.
      const yMid = (yTop + yBottom) / 2;
      const lineas = [];
      if (inicialVisible) {
        const bat = g[pfx + 'InicialBatiplane'] || 0;
        const resto = g[pfx + 'Inicial'] - bat;
        if (resto > 0) lineas.push({ texto: f(resto), color: colorInicial });
        if (bat > 0) lineas.push({ texto: f(bat), color: _COLOR_INICIAL_BATIPLANE });
      }
      if (progVisible) {
        const bat = g[pfx + 'ProgramadoBatiplane'] || 0;
        const resto = g[pfx + 'Programado'] - bat;
        if (resto > 0) lineas.push({ texto: f(resto), color: colorProg });
        if (bat > 0) lineas.push({ texto: f(bat), color: colorProgBat });
      }
      if (fundVisible) {
        const bat = g[pfx + 'FundidoBatiplane'] || 0;
        const resto = g[pfx + 'Fundido'] - bat;
        if (resto > 0) lineas.push({ texto: f(resto), color: colorFund });
        if (bat > 0) lineas.push({ texto: f(bat), color: colorFundBat });
      }
      const gap = fontSize + 2;
      const yIni = yMid - (lineas.length - 1) * gap / 2;
      lineas.forEach((linea, i) => {
        ctx.fillStyle = linea.color;
        ctx.fillText(linea.texto, xTip - 6, yIni + i * gap);
      });
    });
    ctx.restore();
  },
};
Chart.register(_grupoLlavePlugin);

// Texto de resumen (arriba del grafico): togglear "Programado"/"Fundido" en
// la leyenda saca esa serie de los totales y de su propio renglon, no solo
// de las barras -- son los mismos numeros, calculados con la misma logica.
function _pendFundirResumenTexto(chart, data, modo) {
  const inicialVisible = chart.isDatasetVisible(0);
  const progVisible = chart.isDatasetVisible(1);
  const fundVisible = chart.isDatasetVisible(2);
  const segTotal = (d, campo) =>
    (inicialVisible ? (d.inicial[campo] || 0) : 0) + (progVisible ? (d.programado[campo] || 0) : 0) + (fundVisible ? (d.fundido[campo] || 0) : 0);
  const grupoTotal = (grupo, campo) => data.reduce((s, d) => s + (d[grupo][campo] || 0), 0);
  const totalPzsSinPeso = data.reduce((s, d) => s + segTotal(d, 'piezas_sin_peso'), 0);
  const totalPzsSinPrecio = data.reduce((s, d) => s + segTotal(d, 'piezas_sin_precio'), 0);
  const totalOts = data.reduce((s, d) => s + segTotal(d, 'ots'), 0);
  const totalPzs = data.reduce((s, d) => s + segTotal(d, 'piezas_pendientes'), 0);
  const totalKg  = data.reduce((s, d) => s + segTotal(d, 'kg_pendientes'), 0);
  const totalPesos = data.reduce((s, d) => s + segTotal(d, 'pesos_pendientes'), 0);
  const sufijoMonto = modo === 'kg' ? (' · ' + fmtKg(totalKg)) : modo === 'pesos' ? (' · ' + fmtPesos(totalPesos)) : '';
  const resumenGrupo = (grupo, nombre) => nombre + ': ' + fmt(grupoTotal(grupo, 'ots')) + ' OT' + (grupoTotal(grupo, 'ots') !== 1 ? 's' : '')
    + ' · ' + fmt(grupoTotal(grupo, 'piezas_pendientes')) + ' piezas'
    + (modo === 'kg' ? ' · ' + fmtKg(grupoTotal(grupo, 'kg_pendientes'))
       : modo === 'pesos' ? ' · ' + fmtPesos(grupoTotal(grupo, 'pesos_pendientes')) : '');
  const partes = [];
  if (inicialVisible) partes.push(resumenGrupo('inicial', 'Inicial'));
  if (progVisible) partes.push(resumenGrupo('programado', 'Programado'));
  if (fundVisible) partes.push(resumenGrupo('fundido', 'Fundido'));
  return totalOts + ' OT' + (totalOts !== 1 ? 's' : '') + ' activas · ' + fmt(totalPzs) + ' piezas'
    + sufijoMonto
    + (modo === 'kg' && totalPzsSinPeso > 0 ? ' · ⚠ ' + fmt(totalPzsSinPeso) + ' sin peso cargado (kg incompleto)' : '')
    + (modo === 'pesos' && totalPzsSinPrecio > 0 ? ' · ⚠ ' + fmt(totalPzsSinPrecio) + ' sin precio cargado ($ incompleto)' : '')
    + (partes.length ? ' — ' + partes.join(' · ') : '');
}

// afterUpdate (no solo al crear el grafico) para que un toggle de leyenda
// -- que dispara su propio chart.update() interno -- deje el texto sincronizado
// sin que cada call site tenga que acordarse de llamarlo a mano.
const _pendFundirSubtituloPlugin = {
  id: 'pendFundirSubtitulo',
  afterUpdate(chart, args, opts) {
    if (!opts || !opts.subtitleId) return;
    const sub = $id(opts.subtitleId);
    if (sub) sub.textContent = _pendFundirResumenTexto(chart, opts.data, opts.modo);
  },
};
Chart.register(_pendFundirSubtituloPlugin);

// Config compartida entre el grafico interactivo (tema oscuro, en pantalla) y
// el que se genera aparte para imprimir (fondo blanco, letra mas grande) --
// print:true cambia colores/tamaños, nunca la logica de los pasos de la regla.
// modo: 'piezas' | 'kg' | 'pesos'.
function _pendFundirChartConfig(data, modo, print, subtitleId) {
  const { ordenada, grupos } = _ordenarPorGrupos(data);
  data = ordenada;
  const campoValor = { piezas: 'ots', kg: 'kg_pendientes', pesos: 'pesos_pendientes' }[modo];
  const valorSegmento = (seg) => seg[campoValor] || 0;
  const etiquetaSegmento = (v) => modo === 'kg' ? fmtKg(v) : modo === 'pesos' ? fmtPesos(v) : fmt(v) + ' OT' + (v !== 1 ? 's' : '');
  const inicialVals = data.map(d => valorSegmento(d.inicial));
  const progVals = data.map(d => valorSegmento(d.programado));
  const fundVals = data.map(d => valorSegmento(d.fundido));
  // Batiplane, mismo valor/metrica que la barra entera (kg u OTs segun el
  // modo) -- se resalta con otro color en la punta de la barra, nunca se resta.
  const inicialBatVals = data.map(d => valorSegmento(d.inicial.batiplane || {}));
  const progBatVals = data.map(d => valorSegmento(d.programado.batiplane || {}));
  const fundBatVals = data.map(d => valorSegmento(d.fundido.batiplane || {}));
  // Barras agrupadas (lado a lado), no apiladas -- cada una con su propio
  // total, asi que el paso de la regla sale del maximo de CUALQUIER barra
  // individual, no de la suma de las dos.
  const etiquetas = [inicialVals.map(etiquetaSegmento), progVals.map(etiquetaSegmento), fundVals.map(etiquetaSegmento)];
  const { major, minor, subdiv } = _reglaPasos(Math.max(...inicialVals, ...progVals, ...fundVals, 0));
  const esMayor = (val) => Math.round(val / minor) % subdiv === 0;

  const opts = chartDefaults();
  opts.indexAxis = 'y';
  opts.plugins.legend.display = true;
  opts.plugins.legend.labels = { color: print ? '#000' : '#8b929e', font: { size: print ? 13 : 11 } };
  opts.layout = { padding: {
    right: print ? 76 : 56, // lugar para el numero total al final de cada barra
    left: grupos.length ? (print ? 110 : 90) : 0, // lugar para la llave + kg del grupo
  } };
  opts.plugins.reglaBarra = {
    minor, subdiv, etiquetas, print,
    batiplaneValores: [inicialBatVals, progBatVals, fundBatVals],
    batiplaneColores: [
      print ? _COLOR_INICIAL_BATIPLANE_PRINT : _COLOR_INICIAL_BATIPLANE,
      print ? _COLOR_PROGRAMADO_BATIPLANE_PRINT : _COLOR_PROGRAMADO_BATIPLANE,
      print ? _COLOR_FUNDIDO_BATIPLANE_PRINT : _COLOR_FUNDIDO_BATIPLANE,
    ],
  };
  opts.plugins.grupoLlave = { grupos, print, modo };
  // Solo en pantalla -- la imagen de impresion es una captura estatica sin
  // leyenda clickeable, no tiene sentido sincronizar un texto que no cambia.
  if (!print && subtitleId) opts.plugins.pendFundirSubtitulo = { data, modo, subtitleId };
  if (print) { opts.animation = false; opts.responsive = false; }
  // Clic en una barra -- Programado o Fundido de un material -- abre la
  // lista de OTs detras de ese numero. No tiene sentido en la imagen impresa.
  if (!print) {
    opts.onHover = (evt, elements) => { evt.native.target.style.cursor = elements.length ? 'pointer' : 'default'; };
    opts.onClick = (evt, elements) => {
      if (!elements || !elements.length) return;
      const { datasetIndex, index } = elements[0];
      const d = data[index];
      const estados = ['I', 'P', 'F'];
      const nombres = ['Inicial', 'Programado', 'Fundido'];
      const campos = ['inicial', 'programado', 'fundido'];
      const estado = estados[datasetIndex];
      const nombre = nombres[datasetIndex];
      const seg = d[campos[datasetIndex]];
      if (!seg || !seg.ots) return;
      _abrirPendFundirDetalle(`${nombre} — Material ${d.material} (${d.codigo})`, { codigo: d.codigo, estado });
    };
  }
  // Eje tipo regla: paso mayor "prolijo" (1/2/5×10^n) segun el maximo actual, con
  // 5 subdivisiones menores mas finas y sin etiqueta -- las marcas mayores, mas
  // oscuras y con el numero, son las que se leen de un vistazo.
  // Chart.js llama font/color en una fase interna de medicion de ancho del eje
  // (_maxDigits) donde ctx.tick todavia no existe -- sin el chequeo esto tira
  // undefined.value y rompe el build entero, dejando el canvas vacio.
  opts.scales.x = {
    min: 0,
    ticks: {
      stepSize: minor,
      color: (ctx) => (ctx.tick && esMayor(ctx.tick.value)) ? (print ? '#000' : '#c9d1d9') : (print ? '#666' : '#484f58'),
      font: (ctx) => (ctx.tick && esMayor(ctx.tick.value)) ? { size: print ? 13 : 11, weight: '700' } : { size: print ? 11 : 9 },
      callback: (val) => esMayor(val) ? fmt(Math.round(val)) : '',
    },
    grid: {
      color: (ctx) => (ctx.tick && esMayor(ctx.tick.value)) ? (print ? '#999' : '#3d4450') : (print ? '#ddd' : '#21262d'),
      lineWidth: (ctx) => (ctx.tick && esMayor(ctx.tick.value)) ? 1.5 : 1,
    },
  };
  if (print) {
    opts.scales.y = { ticks: { color: '#000', font: { size: 14, weight: '700' } }, grid: { color: '#eee' } };
  }
  opts.plugins.tooltip = { callbacks: {
    title: (items) => data[items[0].dataIndex].material,
    label: (ctx) => {
      const campos = ['inicial', 'programado', 'fundido'];
      const nombres = ['Inicial', 'Programado', 'Fundido'];
      const seg = data[ctx.dataIndex][campos[ctx.datasetIndex]];
      const nombre = nombres[ctx.datasetIndex];
      if (modo === 'piezas') {
        return nombre + ': ' + fmt(seg.ots) + ' OT' + (seg.ots !== 1 ? 's' : '') + ' · ' + fmt(seg.piezas_pendientes) + ' piezas';
      }
      const f = modo === 'kg' ? fmtKg : fmtPesos;
      return nombre + ': ' + fmt(seg.piezas_pendientes) + ' piezas · ' + f(seg[campoValor]);
    },
    afterLabel: (ctx) => {
      const seg = data[ctx.dataIndex][['inicial', 'programado', 'fundido'][ctx.datasetIndex]];
      const lineas = [];
      const bat = seg.batiplane;
      if (bat && bat.ots) {
        const f = modo === 'kg' ? fmtKg : modo === 'pesos' ? fmtPesos : null;
        lineas.push('◆ De los cuales, Batiplane: ' + fmt(bat.ots) + ' OT' + (bat.ots !== 1 ? 's' : '')
          + (f ? ' · ' + f(bat[campoValor]) : ' · ' + fmt(bat.piezas_pendientes) + ' piezas'));
      }
      if (modo === 'kg' && seg.piezas_sin_peso) {
        lineas.push(seg.piezas_sin_peso >= seg.piezas_pendientes
          ? '⚠ Sin ninguna pieza con peso cargado — kg desconocido'
          : '⚠ ' + fmt(seg.piezas_sin_peso) + ' de ' + fmt(seg.piezas_pendientes) + ' piezas sin peso cargado — kg incompleto');
      }
      if (modo === 'pesos' && seg.piezas_sin_precio) {
        lineas.push(seg.piezas_sin_precio >= seg.piezas_pendientes
          ? '⚠ Sin ninguna pieza con precio cargado — $ desconocido'
          : '⚠ ' + fmt(seg.piezas_sin_precio) + ' de ' + fmt(seg.piezas_pendientes) + ' piezas sin precio cargado — $ incompleto');
      }
      return lineas.length ? lineas : undefined;
    }
  } };

  const sinPesoTotal = (d) => (d.inicial.piezas_sin_peso || 0) + (d.programado.piezas_sin_peso || 0) + (d.fundido.piezas_sin_peso || 0);
  const sinPrecioTotal = (d) => (d.inicial.piezas_sin_precio || 0) + (d.programado.piezas_sin_precio || 0) + (d.fundido.piezas_sin_precio || 0);
  const faltaDato = (d) => (modo === 'kg' && sinPesoTotal(d) > 0) || (modo === 'pesos' && sinPrecioTotal(d) > 0);
  const colorInicial    = print ? _COLOR_INICIAL_PRINT : _COLOR_INICIAL;
  const colorProgramado = print ? _COLOR_PROGRAMADO_PRINT : _COLOR_PROGRAMADO;
  const colorFundido    = print ? _COLOR_FUNDIDO_PRINT : _COLOR_FUNDIDO;

  return {
    type: 'bar',
    data: {
      labels: data.map(d => d.codigo + (faltaDato(d) ? ' ⚠' : '')),
      datasets: [
        { label: 'Inicial',    data: inicialVals, backgroundColor: colorInicial,    borderRadius: 4 },
        { label: 'Programado', data: progVals, backgroundColor: colorProgramado, borderRadius: 4 },
        { label: 'Fundido',    data: fundVals, backgroundColor: colorFundido,    borderRadius: 4 },
      ],
    },
    options: opts,
  };
}

function _drawPendienteFundirChart(canvasId, subtitleId, data) {
  const canvas = $id(canvasId);
  if (!canvas) return;
  if (!data.length) {
    canvas.closest('.chart-box').innerHTML = '<div class="empty">Sin piezas pendientes de fundir</div>';
    return;
  }
  const modo = _pendFundirModo;
  const kgBtn = $id('pend-fundir-kg-btn');
  if (kgBtn) kgBtn.textContent = { piezas: 'Ver en kg', kg: 'Ver en $', pesos: 'Ver en piezas' }[modo];

  // .chart-box trae una altura fija (240px) pensada para los demas graficos
  // de la app -- ahi, con Programado y Fundido lado a lado por cada material,
  // las barras quedaban cada vez mas finas cuanto mas materiales hay pendientes.
  // Alto proporcional a la cantidad de filas para que el grosor de barra no
  // dependa de cuantos materiales entren, con un piso igual al de siempre.
  const chartBox = canvas.closest('.chart-box');
  if (chartBox) chartBox.style.height = Math.max(240, data.length * 46 + 60) + 'px';

  if (_pendFundirCharts[canvasId]) { _pendFundirCharts[canvasId].destroy(); _pendFundirCharts[canvasId] = null; }
  _pendFundirCharts[canvasId] = new Chart(canvas, _pendFundirChartConfig(data, modo, false, subtitleId));
}


function _imprimirPendienteFundir() {
  const ids = _pendFundirIds;
  const data = _pendFundirData;
  if (!ids || !data || !data.length) { alert('Esperá a que termine de cargar el gráfico.'); return; }
  const sub = ids.subtitleId ? $id(ids.subtitleId) : null;
  const subtitle = sub ? sub.textContent : '';
  const now = new Date().toLocaleString('es-AR');

  // Grafico aparte para imprimir (fondo blanco, letra mas grande) en vez del
  // canvas de pantalla -- ese usa colores pensados para el tema oscuro de la
  // app y quedarian invisibles sobre una hoja blanca.
  const printCanvas = document.createElement('canvas');
  printCanvas.width = 1600;
  printCanvas.height = 800;
  const printChart = new Chart(printCanvas, _pendFundirChartConfig(data, _pendFundirModo, true));
  const imgData = printCanvas.toDataURL('image/png');
  printChart.destroy();

  const w = window.open('', '_blank', 'width=900,height=700,scrollbars=yes');
  if (!w) { alert('Permitir ventanas emergentes para usar esta función.'); return; }
  w.document.open();
  w.document.write(`<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<title>Pendiente de fundir por material</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,-apple-system,sans-serif;color:#111;background:#fff;padding:24px 28px}
h1{font-size:17px;font-weight:700;color:#333;margin-bottom:3px}
.period{font-size:11px;color:#888;margin-bottom:16px}
.print-date{font-size:16px;color:#000;font-weight:700}
img{width:100%;display:block;background:#fff;border:1px solid #ddd;border-radius:6px;padding:12px}
@media print{body{padding:0}@page{margin:15mm;size:A4 landscape}}
</style></head><body>
<h1>Pendiente de fundir por material</h1>
<div class="period"><span class="print-date">Impreso: ${now}</span>${subtitle ? ' &middot; ' + subtitle : ''}</div>
<div style="display:flex;align-items:center;gap:6px;font-size:10px;color:#888;margin-bottom:10px">
  <span style="width:9px;height:9px;border-radius:2px;background:${_COLOR_PROGRAMADO_BATIPLANE_PRINT};display:inline-block;flex-shrink:0"></span>
  <span style="width:9px;height:9px;border-radius:2px;background:${_COLOR_FUNDIDO_BATIPLANE_PRINT};display:inline-block;flex-shrink:0"></span>
  <span>Batiplane (color más oscuro, dentro de cada barra)</span>
</div>
<img src="${imgData}" alt="Pendiente de fundir por material">
</body></html>`);
  w.document.close();
  setTimeout(() => w.print(), 500);
}
