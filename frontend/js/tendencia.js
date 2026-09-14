// tendencia.js — Tendencia anual (Entregadas/Rechazadas/Devueltas + la barra
// fantasma de proyeccion Programado+Fundido sobre el año actual). Compartido
// entre index.html y kiosk.html -- unica fuente de esta funcionalidad.
//
// Solo la vista de BARRAS anual esta aca. index.html tiene ademas Curvas
// (drill-down mensual con su propia linea de tiempo) y el drill-down por mes
// al clickear una barra -- eso se queda en index.html porque depende de
// _drillMes, una funcion compartida con otros graficos de piezas/clientes que
// no tiene sentido portar a una pantalla de kiosk sin interaccion. index.html
// sigue llamando a _renderTendenciaBarras para su propia vista de barras (con
// su callback de drill-down); kiosk.html la llama sin callback.
//
// Dependencias que el documento que lo carga debe definir ANTES de este script:
//   $id(id), api(path), fmt(n), fmtKg(n), chartDefaults() y los colores
//   _COLOR_PROGRAMADO/_COLOR_FUNDIDO definidos por pend_fundir.js.
//   Chart (Chart.js UMD, cargado por <script> aparte)

function _proyeccionPartes(proyeccion, useKg) {
  if (!proyeccion) return { inicial: 0, programado: 0, fundido: 0 };
  return useKg
    ? { inicial: proyeccion.inicial?.kg || 0, programado: proyeccion.programado.kg || 0, fundido: proyeccion.fundido.kg || 0 }
    : { inicial: proyeccion.inicial?.piezas || 0, programado: proyeccion.programado.piezas || 0, fundido: proyeccion.fundido.piezas || 0 };
}

// Encima de la barra "Entregadas" de uno o más índices (el año actual en el
// gráfico anual; en el drill-down mensual, cada mes que tenga Programado o
// Fundido con esa fecha de entrega): una barra fantasma punteada que llega
// hasta la proyección de ese punto -- visual, no texto.
const _trendProyeccionPlugin = {
  id: 'trendProyeccion',
  afterDatasetsDraw(chart, args, opts) {
    if (!opts || !opts.items || !opts.items.length) return;
    const meta = chart.getDatasetMeta(0); // dataset "Entregadas"
    const ctx = chart.ctx;
    ctx.save();
    ctx.lineWidth = 1.5;
    ctx.setLineDash([4, 3]);
    for (const item of opts.items) {
      const bar = meta && meta.data[item.index];
      if (!bar) continue;
      const halfW = bar.width / 2;
      let acumulado = item.entregadasActual || 0;
      for (const segmento of [
        { valor: item.inicial || 0, color: _COLOR_INICIAL, borde: _COLOR_INICIAL_PRINT },
        { valor: item.programado || 0, color: _COLOR_PROGRAMADO, borde: _COLOR_PROGRAMADO_PRINT },
        { valor: item.fundido || 0, color: _COLOR_FUNDIDO, borde: _COLOR_FUNDIDO_PRINT },
      ]) {
        if (segmento.valor <= 0) continue;
        const yBase = chart.scales.y.getPixelForValue(acumulado);
        acumulado += segmento.valor;
        const yTop = chart.scales.y.getPixelForValue(acumulado);
        ctx.fillStyle = segmento.color;
        ctx.strokeStyle = segmento.borde;
        ctx.beginPath();
        ctx.rect(bar.x - halfW, yTop, bar.width, yBase - yTop);
        ctx.fill();
        ctx.stroke();
      }
    }
    ctx.restore();
  },
};
Chart.register(_trendProyeccionPlugin);

// Dibuja la barra anual en canvasId. kgBtnId (opcional): boton "Ver kg" a
// sincronizar (texto + oculto si no hay datos en kg). onBarClick(año)
// (opcional): index.html lo usa para el drill-down mensual; kiosk no pasa
// nada, ahi las barras no son clickeables. Devuelve el Chart creado (o null
// si no habia datos/canvas) -- el llamador decide si lo guarda en algun lado.
function _renderTendenciaBarras(canvasId, data, proyeccion, useKg, kgBtnId, onBarClick) {
  const canvas = $id(canvasId);
  if (!canvas || !data || !data.length) return null;
  try { Chart.getChart(canvas)?.destroy(); } catch (_) {}

  const hasKg = data.some(t => (t.kg_entregadas || 0) > 0);
  useKg = useKg && hasKg;
  const sfx = useKg ? ' (kg)' : '';
  const kgBtn = kgBtnId ? $id(kgBtnId) : null;
  if (kgBtn) {
    kgBtn.style.display = hasKg ? '' : 'none';
    kgBtn.textContent = useKg ? 'Ver unidades' : 'Ver kg';
  }

  const opts = chartDefaults();
  opts.layout = { padding: { top: 30 } }; // lugar para la proyección arriba de la barra del año actual
  const añoActualTT = String(new Date().getFullYear());
  const idxActual = data.findIndex(t => t.año === añoActualTT);
  const entregadasActual = idxActual >= 0
    ? (useKg ? (data[idxActual].kg_entregadas || 0) : (data[idxActual].entregadas || 0))
    : 0;
  const partes = _proyeccionPartes(proyeccion, useKg);
  opts.plugins.trendProyeccion = (proyeccion && idxActual >= 0)
    ? { items: [{ index: idxActual, entregadasActual, ...partes }] }
    : null;
  // En la barra Entregadas del año actual, el tooltip desglosa la proyección
  // en Programado y Fundido, igual que Pendientes de fundir.
  opts.plugins.tooltip = { callbacks: { label: (ctx) => {
    const label = ctx.dataset.label || '';
    const val = ctx.parsed.y;
    if (ctx.datasetIndex === 0 && proyeccion && ctx.dataIndex === idxActual) {
      const fmtFn = useKg ? fmtKg : fmt;
      return [
        label + ': ' + fmtFn(val),
        'Inicial: ' + fmtFn(partes.inicial),
        'Programado: ' + fmtFn(partes.programado),
        'Fundido: ' + fmtFn(partes.fundido),
        'Total proyectado: ' + fmtFn(val + partes.inicial + partes.programado + partes.fundido),
      ];
    }
    return label + ': ' + (useKg ? fmtKg(val) : fmt(val));
  } } };
  if (onBarClick) {
    opts.onHover = (e, els) => { try { e.native.target.style.cursor = els.length ? 'pointer' : 'default'; } catch (_) {} };
    opts.onClick = (evt, els) => { if (els.length) onBarClick(data[els[0].index].año); };
  }

  return new Chart(canvas, {
    type: 'bar',
    data: {
      labels: data.map(t => t.año),
      datasets: useKg ? [
        { label: 'Entregadas'+sfx, data: data.map(t=>t.kg_entregadas||0), backgroundColor: '#66bb6abb', borderRadius: 4 },
        { label: 'Rechazadas'+sfx, data: data.map(t=>t.kg_rechazadas||0), backgroundColor: '#ef5350bb', borderRadius: 4 },
        { label: 'Devueltas'+sfx,  data: data.map(t=>t.kg_devueltas ||0), backgroundColor: '#a78bfabb', borderRadius: 4 },
        { label: 'Entregadas - Devueltas'+sfx, data: data.map(t=>t.kg_neta||0), backgroundColor: '#42a5f5bb', borderRadius: 4 },
      ] : [
        { label: 'Entregadas', data: data.map(t=>t.entregadas), backgroundColor: '#66bb6abb', borderRadius: 4 },
        { label: 'Rechazadas', data: data.map(t=>t.rechazadas), backgroundColor: '#ef5350bb', borderRadius: 4 },
        { label: 'Devueltas',  data: data.map(t=>t.devueltas),  backgroundColor: '#a78bfabb', borderRadius: 4 },
        { label: 'Entregadas - Devueltas', data: data.map(t=>t.neta), backgroundColor: '#42a5f5bb', borderRadius: 4 },
      ],
    },
    options: opts,
  });
}
