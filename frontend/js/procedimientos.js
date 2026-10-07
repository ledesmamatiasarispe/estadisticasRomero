// procedimientos.js — Módulo general de "Procedimientos": contenido editable
// tipo blog, por bloques (JSON {blocks:[{type,data},...]}, ver /api/procedimientos
// en main.py). Pensado para que cualquier pestaña de la app pueda mostrar/editar
// un procedimiento sin reinventar nada -- hoy el único consumidor es la pestaña
// Procedimiento de Sinterizado (sinterizado.js), pero este archivo no depende
// de nada específico de ese módulo.
//
// Lectura (_procRenderLectura) + edición por bloques (_procAbrirEditor y
// todo lo que cuelga de ahí, más abajo, incluidos los anexos como un tipo
// más de bloque). La edición NO es Editor.js: el plan original la nombraba así para la Fase
// 3, pero vendorizar esa librería completa con Tools custom por tipo de
// bloque es una obra aparte -- esto cubre el mismo pedido ("editable, como
// un blog") con un formulario simple por tipo de bloque y mover arriba/abajo
// en vez de arrastrar. Si en algún momento hace falta arrastrar de verdad,
// esto se reemplaza sin tocar el modelo de datos (sigue siendo
// {blocks:[{type,data}]}, lo único que cambia es cómo se arma en pantalla).
//
// Además de "colgar" un procedimiento dentro de la pantalla de otro módulo
// (ver _procRegistrar/_procRenderPagina, que es lo que usa hoy Sinterizado),
// este archivo arma su PROPIA pestaña principal del menú lateral
// ("Procedimientos", permiso de página 'procedimientos' en _ALL_SECCIONES):
// un listado de TODOS los procedimientos que la persona puede ver, de
// cualquier módulo -- ver showProcedimientos más abajo.
//
// Dependencias que define index.html ANTES de usar esto: $id, setMain, api,
// _esc, fmtFechaCorta, _AUTH. Los estilos se inyectan solos la primera vez
// (_procEstilos), igual que el resto de los módulos de esta app (ver
// _snEstilos en sinterizado.js) -- a propósito con sus PROPIAS clases
// ('proc-*', no 'sn-*'), para no depender de que sinterizado.js haya corrido
// antes: un consumidor futuro que no sea Sinterizado tiene que poder usar
// este archivo solo.

function _procEstilos() {
  if ($id('proc-style')) return;
  const st = document.createElement('style');
  st.id = 'proc-style';
  st.textContent = `
.proc-dl { display: grid; grid-template-columns: 170px 1fr; gap: 4px 12px; font-size: 13px; margin: 0; }
.proc-dl dt { color: var(--muted); }
.proc-dl dd { margin: 0; white-space: pre-wrap; } /* el "Valor" de un anexo admite salto de linea (pedido explicito) -- ficha nunca tiene \n en su valor (sigue siendo un input de un renglon), asi que esto no le cambia nada */
.proc-steps { display: flex; flex-direction: column; gap: 8px; }
.proc-step { border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; background: var(--surface2); }
.proc-step-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.proc-step-num { flex: none; min-width: 24px; height: 24px; padding: 0 6px; border-radius: 12px; display: flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 700; background: var(--accent-dim); color: var(--accent); }
.proc-step-tit { font-weight: 600; font-size: 13px; }
.proc-step ul { margin: 6px 0 0 34px; padding-left: 16px; font-size: 12px; }
.proc-step li { margin-bottom: 2px; }
.proc-substeps { display: flex; flex-direction: column; gap: 8px; margin: 8px 0 0 20px; padding-left: 12px; border-left: 2px solid var(--border); }
.proc-step-nivel-2 { background: var(--surface); }
.proc-step-nivel-3 { background: var(--surface2); }
.proc-nota { margin: 8px 0 0 34px; padding: 6px 10px; border-left: 3px solid var(--yellow); background: rgba(255,167,38,.09); font-size: 12px; }
.proc-h { margin: 0 0 8px; }
.proc-vacio { color: var(--muted); font-size: 12px; padding: 10px 0; }
.proc-anexo-link { color: var(--accent); text-decoration: underline dotted; cursor: pointer; }
.proc-codigo-badge { display: inline-block; font-size: 11px; font-family: monospace; color: var(--accent); background: var(--accent-dim); border: 1px solid var(--accent); border-radius: 5px; padding: 2px 7px; margin-left: 8px; vertical-align: middle; }
.proc-anexo-modal { position: fixed; inset: 0; background: rgba(0,0,0,.6); display: flex; align-items: center; justify-content: center; z-index: 1000; padding: 16px; }
.proc-anexo-modal-box { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 16px; width: 420px; max-width: 100%; max-height: 80vh; overflow-y: auto; }
.proc-anexo-form { border: 1px dashed var(--border); border-radius: 8px; padding: 12px; margin-top: 14px; }
.proc-anexo-form input { margin-bottom: 6px; }
/* Mismo lenguaje visual que .proc-step (panel con fondo propio), pero
   genérico -- para los campos de header/paragraph/list/table, que antes
   quedaban como controles sueltos dentro del .proc-anexo-form (sin ningún
   panel propio) mientras pasos/ficha ya tenían ese aspecto más prolijo. */
.proc-campo-box { border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; background: var(--surface2); }
.proc-tabla-edit { overflow-x: auto; margin-bottom: 4px; padding-bottom: 2px; }
.proc-tabla-fila { display: flex; gap: 4px; margin-bottom: 4px; align-items: center; }
.proc-tabla-esquina { width: 20px; flex: none; }
.proc-tabla-fila-quitar { width: 20px; flex: none; text-align: center; }
.proc-tabla-col-quitar { flex: 1; min-width: 70px; display: flex; justify-content: center; }
.proc-tabla-celda { flex: 1; min-width: 70px; margin-bottom: 0; }
/* Color de fondo por FILA (no por celda -- ver el comentario de
   _PROC_BLOQUE_DEFAULT.table): un swatch + un botón de "quitar color",
   agrupados en un bloque de ancho fijo para que la fila de encabezado
   (que no tiene datos, solo reserva el espacio) pueda alinearse con un
   placeholder de la MISMA clase, vacío. */
.proc-tabla-fondo { width: 42px; flex: none; display: flex; gap: 2px; align-items: center; }
.proc-tabla-fondo input[type=color] { width: 22px; height: 24px; flex: none; padding: 1px; border: 1px solid var(--border); border-radius: 4px; cursor: pointer; background: var(--surface2); }
.proc-tabla-fondo .sn-quitar { font-size: 13px; }
.proc-tabla-borde-color { width: 26px; height: 26px; flex: none; padding: 1px; border: 1px solid var(--border); border-radius: 4px; cursor: pointer; background: var(--surface2); }
.proc-img-wrap { margin: 8px 0; }
.proc-img { max-width: 100%; border-radius: 8px; border: 1px solid var(--border); display: block; }
.proc-archivo-link { margin: 8px 0; font-size: 13px; }
.proc-grafico-wrap { max-width: 520px; height: 300px; margin: 4px 0; position: relative; }
.proc-diagrama-wrap { width: 100%; min-height: 160px; margin: 4px 0; border: 1px solid var(--border); border-radius: 8px; overflow: auto; }
.sn-quitar { cursor: pointer; opacity: .6; font-weight: 700; font-size: 16px; padding: 0 4px; }
.sn-quitar:hover { opacity: 1; color: var(--red); }
.proc-toolbar { display: flex; gap: 12px; align-items: flex-start; flex-wrap: wrap; background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; margin-bottom: 10px; position: sticky; top: 0; z-index: 5; }
.proc-toolbar-grupo { display: flex; flex-direction: column; gap: 4px; padding-right: 12px; border-right: 1px solid var(--border); }
.proc-toolbar-grupo:last-child { border-right: none; padding-right: 0; }
.proc-toolbar-grupo-titulo { font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: .05em; }
.proc-toolbar-botones { display: flex; gap: 4px; }
.proc-toolbar-btn { display: flex; flex-direction: column; align-items: center; gap: 2px; background: var(--surface2); border: 1px solid var(--border); border-radius: 6px; padding: 4px 9px; cursor: pointer; color: var(--text); min-width: 46px; }
.proc-toolbar-btn:hover { border-color: var(--accent); color: var(--accent); background: var(--accent-dim); }
.proc-toolbar-btn .proc-toolbar-ico { font-size: 16px; line-height: 1.1; }
.proc-toolbar-btn .proc-toolbar-lbl { font-size: 9px; white-space: nowrap; }
.proc-editor-grid-etiquetas { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; font-weight: 600; margin-bottom: 6px; }
.proc-editor-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px 16px; }
.proc-editor-grid.proc-sin-preview { grid-template-columns: 1fr; } /* botón "Ocultar vista previa" -- una sola columna, más ancha para escribir */
.proc-fila-editor, .proc-fila-preview { min-width: 0; }
.proc-fila-preview { border: 1px dashed var(--accent); border-radius: 8px; padding: 10px 12px; background: var(--surface); overflow-y: auto; }
/* Botón "Alinear vista previa" DESACTIVADO ("modo real"): deja de ser una
   grilla de filas pareadas -- dos columnas independientes, cada una con
   sus propios bloques apilados, que fluyen a su altura real (ver
   _procEditorHtml). */
.proc-editor-grid.proc-modo-real { display: flex; align-items: flex-start; gap: 16px; }
.proc-editor-col, .proc-preview-col { flex: 1 1 50%; min-width: 0; display: flex; flex-direction: column; gap: 10px; }
@media (max-width: 900px) {
  .proc-editor-grid { grid-template-columns: 1fr; }
  .proc-editor-grid.proc-modo-real { flex-direction: column; }
  .proc-editor-grid-etiquetas { display: none; } /* en una sola columna, "Editor"/"Así va quedando" sueltos arriba no dicen nada -- cada fila ya alterna las dos */
}
/* Esconder el menú lateral (barra de pestañas principal) mientras se edita,
   para ganar ancho para la pantalla dividida -- mismo mecanismo que ya usa
   el modo kiosko (ver "body.kiosk #sidebar" en index.html): una clase en
   <body>, nada de JS del lado del sidebar. #main ya es flex:1 dentro de
   #layout, así que al esconderse el sidebar #main ocupa el lugar solo. */
body.proc-full #sidebar { display: none !important; }
`;
  document.head.appendChild(st);
}

// Convierte un nombre en un id de anclaje estable (sin acentos, sin
// espacios) -- se usa como clave para encontrar el anexo al clickear una
// palabra clave, y como id de la tarjeta del anexo en el documento.
function _procSlug(s) {
  return String(s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
}

// Busca cada 'nombre' de anexo dentro de 'texto' (sin importar mayúsculas) y
// lo envuelve en un link que abre la ficha de ese anexo -- así, alguien que
// esté leyendo un paso no tiene que ir a buscar el anexo: lo abre ahí mismo.
// Coincidencia de PALABRA COMPLETA (no "Antenas" por "Antena"), con acentos
// contados como letra (si no, "Ó" quedaría como límite de palabra). Si dos
// nombres se superponen, gana el más largo (se prueban en ese orden).
// Siempre escapa el texto primero: lo único que no se escapa es el propio
// <span> que se agrega.
// Insignia azul con el código único del documento (pedido explícito) -- al
// lado del título en el detalle, y en cualquier link que apunte a OTRO
// procedimiento (anexo tipo 'procedimiento'), para saber de un vistazo qué
// código hay que pedir desde otro módulo sin tener que abrirlo.
function _procCodigoBadgeHtml(codigo) {
  return codigo ? `<span class="proc-codigo-badge">${_esc(codigo)}</span>` : '';
}
const _PROC_LETRA = 'A-Za-zÁÉÍÓÚÑÜáéíóúñü0-9_';
function _procEnlazarTexto(texto, anexos) {
  const escapado = _esc(texto || '');
  if (!anexos || !anexos.length) return escapado;
  const ordenados = anexos.slice().sort((a, b) => b.nombre.length - a.nombre.length);
  const patron = ordenados.map(a => a.nombre.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|');
  if (!patron) return escapado;
  const re = new RegExp('(?<![' + _PROC_LETRA + '])(' + patron + ')(?![' + _PROC_LETRA + '])', 'gi');
  return escapado.replace(re, (m) => {
    const anexo = ordenados.find(a => a.nombre.toLowerCase() === m.toLowerCase());
    return anexo ? `<span class="proc-anexo-link" onclick="_procAbrirAnexo('${anexo.slug}')">${m}</span>` : m;
  });
}

// ── Bloques conocidos (Fase 2: los "base" -- header/paragraph/list/table/
// ficha/pasos/anexo). callout/imagen/grafico/diagrama son de la Fase 3
// (necesitan Tools de edición propios); acá, si aparecen, caen en el "sin
// vista" de abajo sin romper nada -- el backend ya los acepta
// (_PROC_TIPOS_BLOQUE en main.py). Los que tienen texto libre (pasos,
// paragraph, list) reciben 'anexos' para ofrecer el link de palabra clave.
function _procBloqueFicha(data) {
  const filas = (data && data.filas) || [];
  const titulo = data && data.titulo;
  return `<div class="card sn-card">${titulo ? `<div class="card-title">${_esc(titulo)}</div>` : ''}
    <dl class="proc-dl">${filas.map(([k, v]) => `<dt>${_esc(k)}</dt><dd>${_esc(v)}</dd>`).join('')}</dl>
  </div>`;
}
// 'nombre' es la palabra clave que _procEnlazarTexto busca en el resto del
// documento (por eso la tarjeta lleva un anclaje propio -- hoy el click
// abre la ventana chica o navega, no hace scroll hasta acá, pero el id
// queda igual por si hace falta). Tres tipos (ver _PROC_ANEXO_TIPOS):
// 'generico' (ficha + archivos, lo de siempre), 'archivo' (ES un solo
// archivo -- reusa _procArchivoHtml, igual que el bloque 'imagen') y
// 'procedimiento' (un acceso directo a otro procedimiento, via go() para
// que la navegación del programa funcione).
function _procBloqueAnexo(data) {
  const d = data || {};
  const nombre = d.nombre || '';
  const tipo = d.tipo || 'generico';
  const anclaje = `id="proc-anexo-${_procSlug(nombre)}"`;
  if (tipo === 'procedimiento') {
    const codigo = d.procedimientoCodigo;
    return `<div class="card sn-card" ${anclaje}><div class="card-title">${_esc(nombre)}</div>
      ${codigo ? `<button type="button" class="page-btn" onclick="go('procedimiento_detalle','${_esc(codigo)}')">&#8594; ${_esc(d.procedimientoTitulo || codigo)}</button>${d.procedimientoTitulo ? ' ' + _procCodigoBadgeHtml(codigo) : ''}`
        : '<div class="proc-vacio">Todav&iacute;a no se eligi&oacute; a qu&eacute; procedimiento apunta.</div>'}
    </div>`;
  }
  if (tipo === 'archivo') {
    const a = d.archivo;
    return `<div class="card sn-card" ${anclaje}><div class="card-title">${_esc(nombre)}</div>
      ${a && a.fileId ? _procArchivoHtml(a.fileId, a.nombre, a.ext) : '<div class="proc-vacio">Todav&iacute;a no se subi&oacute; ning&uacute;n archivo.</div>'}
    </div>`;
  }
  const filas = d.filas || [];
  const archivos = d.archivos || [];
  return `<div class="card sn-card" ${anclaje}><div class="card-title">${_esc(nombre)}</div>
    <dl class="proc-dl">${filas.map(([k, v]) => `<dt>${_esc(k)}</dt><dd>${_esc(v)}</dd>`).join('')}</dl>
    ${archivos.map(a => _procArchivoHtml(a.fileId, a.nombre, a.ext)).join('')}
  </div>`;
}
// Un paso puede tener subpasos (5.1, 5.2...) y cada subpaso sus propios
// subsubpasos (5.1.1, 5.1.2...) -- pedido explícito, tope de 3 niveles en
// total (nunca 5.1.1.1): a partir de ahí _procPasoEditorHtml ya no ofrece
// "Agregar subpaso". El número nunca se guarda -- se calcula aquí mismo,
// en lectura, a partir de la POSICIÓN (prefijo del padre + "." + posición
// +1); si se reordena un paso, el número de todos sus hijos cambia solo.
// Un renglón sin texto es un separador a propósito (ver _procLimpiarDetalles
// más abajo, que lo preserva si está ENTRE dos con texto) -- se pinta como
// renglón vacío de verdad, sin el punto de lista (list-style:none), nunca
// como un <li> con el punto suelto y nada al lado.
function _procDetalleLiHtml(d, anexos) {
  return (d || '').trim()
    ? '<li>' + _procEnlazarTexto(d, anexos) + '</li>'
    : '<li style="list-style:none">&nbsp;</li>';
}
function _procPasoHtml(p, numero, anexos, nivel) {
  const hijos = (p.subpasos || []).map((sp, k) => _procPasoHtml(sp, numero + '.' + (k + 1), anexos, nivel + 1)).join('');
  return `<div class="proc-step proc-step-nivel-${nivel}">
    <div class="proc-step-head"><span class="proc-step-num">${numero}</span><span class="proc-step-tit">${_procEnlazarTexto(p.titulo, anexos)}</span></div>
    <ul>${(p.detalles || []).map(d => _procDetalleLiHtml(d, anexos)).join('')}</ul>
    ${p.nota ? '<div class="proc-nota">' + _procEnlazarTexto(p.nota, anexos) + '</div>' : ''}
    ${(p.archivos || []).map(a => _procArchivoHtml(a.fileId, a.nombre, a.ext)).join('')}
    ${hijos ? `<div class="proc-substeps">${hijos}</div>` : ''}
  </div>`;
}
// Igual que _procPasoHtml pero SIN anidar los hijos adentro -- hace falta
// para el modo "alineada" del editor (ver _procEditorBloquePasosAlineadoFilas
// más abajo), donde cada subpaso es su PROPIA fila de grilla en vez de
// vivir adentro del render de su padre: ahí, pintar los hijos dos veces
// (una acá, otra como su propia fila) los duplicaría en la vista previa.
function _procPasoHtmlPropio(p, numero, anexos, nivel) {
  return `<div class="proc-step proc-step-nivel-${nivel}">
    <div class="proc-step-head"><span class="proc-step-num">${numero}</span><span class="proc-step-tit">${_procEnlazarTexto(p.titulo, anexos)}</span></div>
    <ul>${(p.detalles || []).map(d => _procDetalleLiHtml(d, anexos)).join('')}</ul>
    ${p.nota ? '<div class="proc-nota">' + _procEnlazarTexto(p.nota, anexos) + '</div>' : ''}
    ${(p.archivos || []).map(a => _procArchivoHtml(a.fileId, a.nombre, a.ext)).join('')}
  </div>`;
}
function _procBloquePasos(data, anexos) {
  const titulo = (data && data.titulo) || 'Pasos';
  const pasos = (data && data.pasos) || [];
  const filas = pasos.map((p, i) => _procPasoHtml(p, String(i + 1), anexos, 1)).join('');
  return `<div class="card sn-card"><div class="card-title">${_esc(titulo)}</div><div class="proc-steps">${filas}</div></div>`;
}
// Recorre pasos + subpasos + subsubpasos (los 3 niveles) -- compartido por
// cualquier recorrido que necesite "todos los pasos sin importar el nivel"
// (hoy: el registro de archivos adjuntos, ver _procArchivosDeContenido).
function _procTodosLosPasos(pasos) {
  return (pasos || []).flatMap(p => [p, ..._procTodosLosPasos(p.subpasos)]);
}
function _procBloqueHeader(data) {
  const nivel = Math.min(Math.max(Number((data && data.level) || 2), 1), 4);
  return `<h${nivel} class="proc-h">${_esc((data && data.text) || '')}</h${nivel}>`;
}
// El editor de esta app (_procEditorCamposHtml, más abajo) junta este texto
// con un <textarea> plano, sin barra de formato -- acá no hay HTML de
// confianza que preservar, así que se escapa igual que ficha/list/table (a
// diferencia de 'pasos', este bloque no ofrece el link de palabra clave de
// los anexos -- es simple a propósito, no hace falta todavía).
function _procBloqueParagraph(data) {
  return `<p>${_esc((data && data.text) || '')}</p>`;
}
function _procBloqueList(data) {
  const tag = (data && data.style) === 'ordered' ? 'ol' : 'ul';
  const items = (data && data.items) || [];
  return `<${tag}>${items.map(it => '<li>' + (typeof it === 'string' ? it : ((it && it.content) || '')) + '</li>').join('')}</${tag}>`;
}
// Estilo de tabla tipo Excel (pedido explícito del usuario: "bordes, grosor
// y colores"). Grano elegido a propósito -- borde UNIFORME para TODA la
// tabla (data.borde: {grosor,color}) + color de fondo por FILA (data.
// filas_fondo, un array paralelo a content, mismo índice): no celda por
// celda, que hubiera necesitado un segundo array del mismo tamaño que
// content solo para los estilos y una UI de selección de celda/rango que no
// alcanza el tiempo para probar a fondo. El fondo por fila cubre el caso de
// uso real más pedido (resaltar un encabezado, alternar filas) sin esa
// complejidad. Una tabla vieja (sin 'borde'/'filas_fondo', de antes de este
// cambio) sigue viéndose exactamente igual que siempre: sin estos campos no
// se agrega ningún style nuevo al <td>.
//
// _procColorValido/_procTablaBordeSano sanean estos dos campos ACÁ, en la
// lectura (no solo en el editor): 'data' no está validado por el backend
// más allá de "es un dict" (_proc_contenido_validar en main.py), así que un
// cliente de la API que no pase por este editor podría mandar cualquier
// string -- sin sanear, un color como 'red;}</style>' se inyectaría crudo
// en un atributo style.
function _procColorValido(c) {
  return /^#[0-9a-fA-F]{6}$/.test(String(c || '')) ? c : null;
}
const _PROC_TABLA_BORDE_DEFAULT = { grosor: 1, color: '#30363d' }; // mismo hex que --border del tema oscuro de index.html
function _procTablaBordeSano(borde) {
  const grosor = Math.min(Math.max(parseInt(borde && borde.grosor, 10) || 0, 0), 5);
  const color = _procColorValido(borde && borde.color) || _PROC_TABLA_BORDE_DEFAULT.color;
  return { grosor, color };
}
function _procBloqueTable(data) {
  const filas = (data && data.content) || [];
  const borde = (data && data.borde) ? _procTablaBordeSano(data.borde) : null;
  const bordeCss = borde && borde.grosor > 0 ? `border:${borde.grosor}px solid ${borde.color};` : '';
  const fondos = (data && data.filas_fondo) || [];
  return `<div class="tbl-wrap"><table>${filas.map((fila, f) => {
    const colorFondo = _procColorValido(fondos[f]);
    const estiloCelda = bordeCss + (colorFondo ? `background-color:${colorFondo};` : '');
    const atributo = estiloCelda ? ` style="${estiloCelda}"` : '';
    return '<tr>' + fila.map(c => `<td${atributo}>` + _esc(c) + '</td>').join('') + '</tr>';
  }).join('')}</table></div>`;
}
// Extensiones que se muestran inline como <img> -- el resto (hoy solo pdf,
// ver _PROC_ARCHIVO_TIPOS en main.py) se ofrece como link de descarga/vista
// (_procAbrirArchivo). El <img> arranca SIN src (el GET exige Bearer, no se
// puede poner directo) -- _procCargarImagenes lo llena después, mismo
// patrón que _snCargarFotoActual en sinterizado.js.
const _PROC_EXT_IMAGEN = new Set(['jpg', 'jpeg', 'png', 'gif', 'webp']);
// Compartido por el bloque 'imagen' Y por los 'archivos' de un anexo (ver
// _procBloqueAnexo) -- un mismo archivo subido se muestra/abre siempre
// igual, sin importar en qué bloque está referenciado.
function _procArchivoHtml(fileId, nombre, ext) {
  if (_PROC_EXT_IMAGEN.has((ext || '').toLowerCase())) {
    return `<div class="proc-img-wrap"><img id="proc-img-${_esc(fileId)}" class="proc-img" alt="${_esc(nombre || '')}"></div>`;
  }
  return `<div class="proc-archivo-link"><span class="proc-anexo-link" onclick="_procAbrirArchivo('${_esc(fileId)}')">&#128196; ${_esc(nombre || 'Archivo adjunto')}</span></div>`;
}
function _procBloqueImagen(data) {
  const fileId = data && data.fileId;
  if (!fileId) return '<div class="proc-vacio">Todav&iacute;a no se subi&oacute; ning&uacute;n archivo en este bloque.</div>';
  return _procArchivoHtml(fileId, (data && data.nombre) || '', (data && data.ext) || '');
}
// Normaliza una lista de {fileId,nombre,ext} (filtra entradas sin fileId) --
// usado para juntar los archivos de anexos/pasos en _PROC_ARCHIVOS_ACTUALES.
function _procArchivosDe(lista) {
  return (lista || []).filter(a => a && a.fileId).map(a => ({ fileId: a.fileId, nombre: a.nombre || '', ext: (a.ext || '').toLowerCase() }));
}
// Datos como pares [etiqueta,valor] en 'filas' (mismo formato que ficha/
// anexo -- reusa sus funciones de edición) -- el <canvas> arranca VACÍO,
// _procDibujarGraficos (más abajo) lo llena después con Chart.js, igual
// motivo que _procCargarImagenes con las <img>: recién existe en el DOM
// real después de que el html vuelva de esta función.
function _procBloqueGrafico(data, anexos, idx) {
  const d = data || {};
  const filas = (d.filas || []).filter(([k]) => (k || '').trim());
  if (!filas.length) return '<div class="proc-vacio">Este gr&aacute;fico todav&iacute;a no tiene datos.</div>';
  return `<div class="card sn-card">${d.titulo ? `<div class="card-title">${_esc(d.titulo)}</div>` : ''}
    <div class="proc-grafico-wrap"><canvas id="proc-grafico-${idx}"></canvas></div>
  </div>`;
}
// XML completo de mxGraph (lo que produce/consume el editor de draw.io
// embebido, ver _procDiagramaEditorAbrir más abajo) -- viaja inline en
// 'data', sin archivos ni PROC_IMG_DIR, igual que el texto de ficha/pasos.
// La lectura usa el visor LIVIANO (js/vendor/drawio/js/viewer.min.js, sin
// iframe) en vez de abrir el editor completo -- mismo espíritu que
// Chart.js: aquí solo se dibuja, nunca se puede tocar.
function _procBloqueDiagrama(data) {
  const d = data || {};
  if (!d.xml) return '<div class="proc-vacio">Este diagrama todav&iacute;a no tiene contenido.</div>';
  const cfg = _esc(JSON.stringify({ xml: d.xml, toolbar: 'zoom', resize: true }));
  return `<div class="card sn-card">${d.titulo ? `<div class="card-title">${_esc(d.titulo)}</div>` : ''}
    <div class="proc-diagrama-wrap mxgraph" data-mxgraph="${cfg}"></div>
  </div>`;
}
const _PROC_RENDER = {
  ficha: _procBloqueFicha, anexo: _procBloqueAnexo, pasos: _procBloquePasos, header: _procBloqueHeader,
  paragraph: _procBloqueParagraph, list: _procBloqueList, table: _procBloqueTable, imagen: _procBloqueImagen,
  grafico: _procBloqueGrafico, diagrama: _procBloqueDiagrama,
};
function _procRenderBloque(b, anexos, idx) {
  const f = b && _PROC_RENDER[b.type];
  if (!f) return `<div class="proc-vacio">Este tipo de bloque ("${_esc((b && b.type) || '?')}") todav&iacute;a no tiene vista en esta pantalla.</div>`;
  return f(b.data || {}, anexos, idx);
}
// Guarda los anexos del ÚLTIMO render para que _procAbrirAnexo (disparado
// por un click en un link de palabra clave, mucho después) sepa qué mostrar
// sin tener que volver a recorrer el contenido.
let _PROC_ANEXOS_ACTUALES = [];
// Igual que _PROC_ANEXOS_ACTUALES, pero para los bloques 'imagen' -- hace
// falta el registro porque el link de descarga de un archivo-no-imagen
// (_procAbrirArchivo) solo recibe el fileId en el onclick (nunca el nombre:
// un nombre de archivo real puede traer comillas y romper el atributo), y
// porque _procCargarImagenes necesita la lista completa para precargar cada
// <img> después de este render.
let _PROC_ARCHIVOS_ACTUALES = [];
// Registro de anexos (ver _procEnlazarTexto) a partir de una lista de
// bloques -- compartido entre la lectura PUBLICADA (_procRenderLectura) y
// la vista previa en vivo del editor (_procEditorHtml), que NO puede pisar
// _PROC_ANEXOS_ACTUALES: ese es del documento que ve "Visualizar borrador"/
// la pestaña publicada, no del borrador a medio escribir.
function _procAnexosDe(bloques) {
  return bloques
    .filter(b => b && b.type === 'anexo' && b.data && b.data.nombre)
    .map(b => ({
      nombre: b.data.nombre, slug: _procSlug(b.data.nombre), tipo: b.data.tipo || 'generico',
      filas: b.data.filas || [], archivos: b.data.archivos || [], archivo: b.data.archivo || null,
      procedimientoCodigo: b.data.procedimientoCodigo || null, procedimientoTitulo: b.data.procedimientoTitulo || '',
    }));
}
// Los archivos propios de un bloque 'imagen' Y los que cuelgan DENTRO de
// cada anexo (genérico o tipo 'archivo') o de cada paso individual (ver
// _procEditorPasoArchivoElegir) van al MISMO registro plano -- mismo
// motivo que _procAnexosDe (compartido, nunca pisa el registro global
// fuera de _procRenderLectura).
function _procArchivosDeContenido(bloques) {
  return bloques
    .filter(b => b && b.type === 'imagen' && b.data && b.data.fileId)
    .map(b => ({ fileId: b.data.fileId, nombre: b.data.nombre || '', ext: (b.data.ext || '').toLowerCase() }))
    .concat(_procArchivosDe(bloques.filter(b => b && b.type === 'anexo').flatMap(b => (b.data && b.data.archivos) || [])))
    .concat(_procArchivosDe(bloques.filter(b => b && b.type === 'anexo' && b.data && b.data.archivo).map(b => b.data.archivo)))
    .concat(_procArchivosDe(_procTodosLosPasos(bloques.filter(b => b && b.type === 'pasos').flatMap(b => (b.data && b.data.pasos) || [])).flatMap(p => (p && p.archivos) || [])));
}
function _procRenderLectura(contenido) {
  const bloques = (contenido && contenido.blocks) || [];
  _PROC_ANEXOS_ACTUALES = _procAnexosDe(bloques);
  _PROC_ARCHIVOS_ACTUALES = _procArchivosDeContenido(bloques);
  if (!bloques.length) return '<div class="proc-vacio">Este procedimiento todav&iacute;a no tiene contenido.</div>';
  const html = bloques.map((b, idx) => _procRenderBloque(b, _PROC_ANEXOS_ACTUALES, idx)).join('');
  _procCargarImagenes(); // fire-and-forget: para cuando resuelva, el html ya está en el DOM (mismo motivo que _snCargarFotoActual)
  // Los <canvas> TAMPOCO existen todavía en el DOM real (este html es un
  // string) -- a diferencia del fetch de las imágenes, dibujar un Chart.js
  // es sincrónico, así que el truco para esperar al DOM es un setTimeout(0)
  // en vez de un await natural.
  setTimeout(() => _procDibujarGraficos(contenido), 0);
  // El visor de diagramas se carga a demanda (no fijo en index.html como
  // Chart.js: este bloque es mucho menos frecuente) -- solo si de verdad
  // hay un bloque 'diagrama' en este contenido.
  if (bloques.some(b => b && b.type === 'diagrama')) setTimeout(_procProcesarDiagramas, 0);
  return html;
}
// Precarga cada <img> de un bloque 'imagen' (fetch con Bearer + blob, el GET
// exige sesión). Los que no son imagen (pdf) no se precargan -- se abren a
// demanda desde _procAbrirArchivo, no tiene sentido bajarlos sin que se pidan.
async function _procCargarImagenes() {
  if (!_procActual || !_procActual.id) return;
  const pid = _procActual.id;
  for (const a of _PROC_ARCHIVOS_ACTUALES) {
    if (!_PROC_EXT_IMAGEN.has(a.ext)) continue;
    _procCargarUnaImagen(pid, a.fileId);
  }
}
async function _procCargarUnaImagen(pid, fileId) {
  try {
    const resp = await fetch('/api/procedimientos/' + pid + '/imagenes/' + fileId, { headers: { Authorization: 'Bearer ' + _AUTH.token } });
    if (!resp.ok) return;
    const blob = await resp.blob();
    const img = $id('proc-img-' + fileId);
    if (img) img.src = URL.createObjectURL(blob);
  } catch (e) { /* silencioso, como _snCargarFotoActual: si no carga, queda el <img> sin src */ }
}
// Dibuja (o redibuja) cada bloque 'grafico' con Chart.js, por índice de
// bloque (mismo índice que usó _procBloqueGrafico para el id del <canvas>).
// Si Chart.js no está cargado (ej. harness de pruebas en Node) no hace
// nada -- mismo criterio defensivo que ya usa el resto de la app
// (`if (ctx && window.Chart)`, ver index.html). Chart.getChart(canvas) es el
// mismo patrón de "destruir antes de recrear" que usan TODOS los demás
// gráficos de la app (nunca una instancia propia guardada en una variable).
const _PROC_COLORES_GRAFICO = ['#4fc3f7', '#ffa726', '#66bb6a', '#ef5350', '#ab47bc', '#8d6e63', '#26a69a', '#d4e157', '#5c6bc0', '#ec407a'];
function _procDibujarGraficos(contenido) {
  if (typeof Chart === 'undefined') return;
  const bloques = (contenido && contenido.blocks) || [];
  bloques.forEach((b, idx) => {
    if (!b || b.type !== 'grafico') return;
    const canvas = $id('proc-grafico-' + idx);
    if (!canvas) return;
    try { Chart.getChart(canvas)?.destroy(); } catch (e) { /* nada que destruir */ }
    const filas = ((b.data && b.data.filas) || []).filter(([k]) => (k || '').trim());
    if (!filas.length) return;
    const etiquetas = filas.map(([k]) => k);
    const valores = filas.map(([, v]) => Number(String(v).replace(',', '.')) || 0);
    const tipo = (b.data && b.data.tipo) || 'bar';
    const esCircular = tipo === 'pie' || tipo === 'doughnut';
    new Chart(canvas, {
      type: tipo,
      data: { labels: etiquetas, datasets: [{
        label: (b.data && b.data.titulo) || '',
        data: valores,
        backgroundColor: etiquetas.map((_, k) => _PROC_COLORES_GRAFICO[k % _PROC_COLORES_GRAFICO.length]),
        borderColor: esCircular ? undefined : _PROC_COLORES_GRAFICO[0],
      }] },
      // maintainAspectRatio:false + el alto FIJO de .proc-grafico-wrap (ver
      // _procEstilos) es lo que evita el loop de achicamiento de Chart.js:
      // sin un alto propio, el contenedor se mide por el <canvas> y el
      // <canvas> se mide por el contenedor -- cada resize (ej. al pasar el
      // mouse, por el tooltip) los hace más chicos a los dos, de a poco.
      options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: esCircular } } },
    });
  });
}
// ── Visor liviano de diagramas (bloque 'diagrama', lectura) ────────────────
// A demanda, no fijo en index.html como Chart.js -- este bloque es mucho
// menos frecuente, no vale la pena pagar su carga en cada pantalla que no
// lo usa. window.onDrawioViewerLoad (no-op) evita que el propio script se
// auto-procese al cargar (lo hace por su cuenta si no encuentra ese
// callback definido) -- se prefiere llamarlo a mano siempre, en
// _procProcesarDiagramas, así el mismo código sirve para la primera vez
// (recién cargado) y para las siguientes (ya cargado, solo re-procesar).
let _procDiagramaVisorCargando = null;
function _procCargarVisorDiagramas() {
  if (typeof GraphViewer !== 'undefined') return Promise.resolve();
  if (_procDiagramaVisorCargando) return _procDiagramaVisorCargando;
  _procDiagramaVisorCargando = new Promise((resolve) => {
    window.onDrawioViewerLoad = () => {};
    const s = document.createElement('script');
    s.src = '/js/vendor/drawio/js/viewer.min.js';
    s.onload = resolve;
    s.onerror = resolve; // si falla, GraphViewer sigue sin existir -- mismo criterio defensivo que Chart.js (if (typeof Chart === 'undefined') return;)
    document.head.appendChild(s);
  });
  return _procDiagramaVisorCargando;
}
// processElements() de por sí es idempotente (reescanea TODOS los
// `.mxgraph` del documento y los vuelve a dibujar desde cero, sin importar
// si ya estaban dibujados) -- se puede llamar de nuevo sin cuidado especial
// cada vez que hay contenido nuevo en el DOM.
function _procProcesarDiagramas() {
  _procCargarVisorDiagramas().then(() => {
    if (typeof GraphViewer !== 'undefined') GraphViewer.processElements();
  });
}
// Un archivo que no es imagen (hoy, pdf) se abre en una pestaña nueva --
// mismo fetch+blob que arriba, pero a demanda (click) en vez de precargado.
async function _procAbrirArchivo(fileId) {
  const archivo = _PROC_ARCHIVOS_ACTUALES.find(a => a.fileId === fileId);
  if (!archivo || !_procActual || !_procActual.id) return;
  try {
    const resp = await fetch('/api/procedimientos/' + _procActual.id + '/imagenes/' + fileId, { headers: { Authorization: 'Bearer ' + _AUTH.token } });
    if (!resp.ok) throw new Error('No se pudo abrir');
    const blob = await resp.blob();
    window.open(URL.createObjectURL(blob), '_blank');
  } catch (e) {
    alert('No se pudo abrir el archivo.');
  }
}

// ── Ventana chica del anexo (al clickear su palabra clave) ─────────────────
// Mismo patrón que _openFotoLightbox en index.html: se crea un <div> nuevo,
// se cuelga de document.body, y se cierra clickeando afuera, el botón, o
// Escape -- sin depender de $id/querySelector (una sola referencia por
// closure alcanza, nunca hay más de un modal de anexo abierto a la vez).
let _procAnexoModalActual = null;
function _procCerrarAnexoModal() {
  if (_procAnexoModalActual) { _procAnexoModalActual.remove(); _procAnexoModalActual = null; }
}
function _procAbrirAnexo(slug) {
  const anexo = _PROC_ANEXOS_ACTUALES.find(a => a.slug === slug);
  if (!anexo) return;
  // Tipo 'procedimiento': no hay ventana chica que mostrar -- se navega
  // directo (go(), para que atrás/adelante del programa funcionen) al
  // procedimiento elegido. Si todavía no se eligió ninguno, no hace nada
  // (ver _procBloqueAnexo: la tarjeta ya avisa que falta elegir).
  if (anexo.tipo === 'procedimiento') {
    if (anexo.procedimientoCodigo) go('procedimiento_detalle', anexo.procedimientoCodigo);
    return;
  }
  _procCerrarAnexoModal();
  const ov = document.createElement('div');
  ov.className = 'proc-anexo-modal';
  // 'archivo': la ventana solo muestra ESE archivo (sin la <dl>, no hay
  // filas en este tipo). 'generico': lo de siempre.
  const esArchivo = anexo.tipo === 'archivo';
  const archivosParaCargar = esArchivo ? (anexo.archivo && anexo.archivo.fileId ? [anexo.archivo] : []) : (anexo.archivos || []);
  const cuerpo = esArchivo
    ? (archivosParaCargar.length ? _procArchivoHtml(archivosParaCargar[0].fileId, archivosParaCargar[0].nombre, archivosParaCargar[0].ext) : '<div class="proc-vacio">Todav&iacute;a no se subi&oacute; ning&uacute;n archivo.</div>')
    : `<dl class="proc-dl">${(anexo.filas || []).map(([k, v]) => `<dt>${_esc(k)}</dt><dd>${_esc(v)}</dd>`).join('')}</dl>${archivosParaCargar.map(a => _procArchivoHtml(a.fileId, a.nombre, a.ext)).join('')}`;
  ov.innerHTML = `<div class="proc-anexo-modal-box">
    <div class="card-title">${_esc(anexo.nombre)}</div>
    ${cuerpo}
    <button type="button" class="page-btn" onclick="_procCerrarAnexoModal()" style="margin-top:10px">Cerrar</button>
  </div>`;
  ov.onclick = (e) => { if (e.target === ov) _procCerrarAnexoModal(); };
  document.addEventListener('keydown', function esc(e) { if (e.key === 'Escape') { _procCerrarAnexoModal(); document.removeEventListener('keydown', esc); } });
  document.body.appendChild(ov);
  _procAnexoModalActual = ov;
  // Las <img> recien se cuelgan del DOM arriba: cargarlas (fetch+blob) recien
  // ahora, igual que _procCargarImagenes para el documento principal.
  if (_procActual && _procActual.id) {
    for (const a of archivosParaCargar) { if (_PROC_EXT_IMAGEN.has((a.ext || '').toLowerCase())) _procCargarUnaImagen(_procActual.id, a.fileId); }
  }
}

// ── Ventana chica del borrador (botón "Visualizar borrador") ──────────────
// Comparación lado a lado: izquierda lo publicado, derecha el borrador --
// misma idea que un diff, sin serlo de verdad (no resalta diferencia por
// diferencia, solo pone los dos documentos completos uno junto al otro).
// Mismo patrón de modal que el anexo (una referencia por closure, se cierra
// clickeando afuera/el botón/Escape). A diferencia de ese, acá SÍ hace falta
// volver a llamar _procRenderLectura (dos veces: una por columna) -- y
// _procRenderLectura pisa _PROC_ANEXOS_ACTUALES/_PROC_ARCHIVOS_ACTUALES de
// paso (son el registro del ÚLTIMO render, para que los links de palabra
// clave sepan qué abrir). Se llama primero la izquierda y después la
// derecha a propósito, así mientras el modal está abierto esos registros
// quedan apuntando al borrador (lo que probablemente se está mirando): si
// no se resincronizan con lo publicado al CERRAR, un anexo de la página
// real (que sigue visible detrás) terminaría abriendo los datos del
// borrador en vez de los suyos.
//
// Límite conocido, no resuelto: si las dos columnas tienen un bloque
// 'grafico' en el MISMO índice, los dos <canvas> comparten id (el id de
// _procBloqueGrafico es por índice de bloque, no por columna) -- el
// segundo dibujo (ver _procRenderLectura) termina pisando el primero en
// vez de dibujar el suyo. No pasa con ningún otro tipo de bloque. Arreglarlo
// de verdad implica poder pasarle un prefijo de id a _procRenderLectura /
// _procDibujarGraficos, que hoy no lo tienen -- se deja así hasta que un
// caso real lo necesite, en vez de tocar esas dos funciones (las usa toda
// la pantalla principal) para esta vista chica de comparación.
let _procBorradorModalActual = null;
function _procCerrarBorradorModal() {
  if (!_procBorradorModalActual) return;
  _procBorradorModalActual.remove();
  _procBorradorModalActual = null;
  if (_procActual && _procActual.contenido) _procRenderLectura(_procActual.contenido);
}
function _procAbrirBorradorModal() {
  if (!_procActual || !_procActual.borrador_contenido) return;
  _procCerrarBorradorModal();
  const ov = document.createElement('div');
  ov.className = 'proc-anexo-modal';
  const publicadoHtml = _procRenderLectura(_procActual.contenido);
  const borradorHtml = _procRenderLectura(_procActual.borrador_contenido);
  ov.innerHTML = `<div class="proc-anexo-modal-box" style="width:min(96vw,980px)">
    <div class="card-title">Comparar borrador</div>
    <div style="display:flex;gap:16px;flex-wrap:wrap;max-height:70vh;overflow-y:auto">
      <div style="flex:1;min-width:260px">
        <div class="proc-vacio" style="font-weight:600;margin-bottom:6px">Publicado (Rev.${String(_procActual.revision || 0).padStart(3, '0')})</div>
        ${publicadoHtml}
      </div>
      <div style="flex:1;min-width:260px;border-left:1px solid var(--border);padding-left:16px">
        <div class="proc-vacio" style="font-weight:600;margin-bottom:6px;color:var(--yellow)">Borrador${_procActual.borrador_titulo ? ': ' + _esc(_procActual.borrador_titulo) : ''}</div>
        ${borradorHtml}
      </div>
    </div>
    <button type="button" class="page-btn" onclick="_procCerrarBorradorModal()" style="margin-top:10px">Cerrar</button>
  </div>`;
  ov.onclick = (e) => { if (e.target === ov) _procCerrarBorradorModal(); };
  document.addEventListener('keydown', function esc(e) { if (e.key === 'Escape') { _procCerrarBorradorModal(); document.removeEventListener('keydown', esc); } });
  document.body.appendChild(ov);
  _procBorradorModalActual = ov;
}

// Mismo permiso transversal para TODO lo editable de este módulo (el editor
// de contenido entero, incluidos los anexos como un tipo de bloque más) --
// ver _require_procedimientos_editar en main.py.
function _procPuedeEditar() {
  // Mientras corre el recorrido guiado ("Ayuda", ver el final de este
  // archivo) se muestra SIEMPRE la vista de alguien que puede editar, sin
  // importar el permiso real de quien está mirando -- es la única forma de
  // explicar el flujo completo (Modificar/Guardar/Aprobar...) a cualquiera.
  // No es un riesgo: el overlay del recorrido bloquea los clics de fondo, y
  // además cada función que de verdad llama a la api (_procAbrirEditor,
  // _procConfirmarEnvio, _procAprobarBorrador...) chequea _procTourActivo
  // aparte, como segunda barrera.
  if (_procTourActivo) return true;
  return !!(_AUTH.user && (_AUTH.user.is_admin || (_AUTH.user.secciones || []).includes('procedimientos_editar')));
}

// Estado del módulo: qué procedimiento está montado, dónde, y a quién
// avisarle cuando se guarda un cambio -- así el consumidor (sinterizado.js
// hoy) no necesita saber nada del editor, solo pedir que se monte un
// código en un contenedor (ver _procRegistrar más abajo).
let _procActual = null;
let _procContenedorId = null;
let _procOnActualizar = null;

function _procRefrescar(id, html) { const el = $id(id); if (el) el.innerHTML = html; }
function _procRefrescarse() { if (_procContenedorId) _procRefrescar(_procContenedorId, _procRenderPagina()); }

// El consumidor llama esto UNA vez que ya tiene el procedimiento cargado
// (ver _snCargarProcPg85106) y un <div id="..."> donde mostrarlo. 'onActualizar'
// es opcional: si el consumidor guarda su propia copia del procedimiento
// (como _SN.procPg85106), este callback la mantiene al día después de
// guardar un cambio, sin que este archivo necesite saber su nombre.
function _procRegistrar(proc, contenedorId, onActualizar) {
  _procActual = proc;
  _procContenedorId = contenedorId;
  _procOnActualizar = onActualizar || null;
  _procEditor = null;
  _procBorradorModificadoEn = null;
  clearTimeout(_procAutoguardadoTimer);
  clearTimeout(_procPreviewTimer);
  _procPreviewVisible = true; // arranca mostrada otra vez en cada documento nuevo
  _procPreviewAlineada = false; // arranca en modo real (independiente) otra vez en cada documento nuevo
  document.body.classList.remove('proc-full'); // por si se navegó a otro documento con el menú escondido
  _procAutoguardadoEnCurso = false;
  _procAutoguardadoPendiente = false;
  _PROC_LISTA_PARA_ANEXO = null; // se vuelve a cargar por si cambió de procedimiento (cambia cuál se excluye del selector)
}

function _procRevisionHtml(p) {
  return `<div style="color:var(--muted);font-size:12px;margin-bottom:8px">Rev.${String(p.revision || 0).padStart(3, '0')}</div>`;
}
function _procRenderPagina() {
  const p = _procActual;
  if (!p) return '<div class="proc-vacio">Cargando...</div>';
  if (p.error) return `<div class="proc-vacio" style="color:var(--red)">${_esc(p.error)}</div>`;
  if (_procEditor) return `${_procRevisionHtml(p)}${_procEditorHtml()}`;
  const lectura = _procRevisionHtml(p) + _procRenderLectura(p.contenido);
  if (!_procPuedeEditar()) return `${lectura}${p.borrador_estado ? _procBannerBorradorHtml(p) : ''}`;
  return `${lectura}${_procHerramientasHtml(p)}`;
}
// Vista previa de UN bloque del borrador, ya limpio (ver
// _procEditorLimpiarBloque) -- 'anexos' es el registro de ESTE borrador
// (_procAnexosDe sobre todos los bloques limpios), nunca
// _PROC_ANEXOS_ACTUALES (ese es del documento publicado/"Visualizar
// borrador", no de lo que se está escribiendo ahora).
function _procVistaPreviaBloqueHtml(bloqueLimpio, anexos, idx) {
  return _procRenderBloque(bloqueLimpio, anexos, idx) || '<div class="proc-vacio">&nbsp;</div>';
}
// Repinta SOLO las celdas de vista previa (nunca el formulario de edición):
// si repintara todo, cada letra tipeada en un <input>/<textarea> perdería
// el foco y el cursor saltaría al principio -- mismo motivo por el que
// _procEditorCampo/_procEditorTitulo (y la mayoría de los setters de
// campo) llaman a _procSucio(false) en vez de _procRefrescarse(). Como
// cada celda de vista previa vive en la MISMA fila de grilla que su
// bloque del editor (ver _procEditorHtml/.proc-editor-grid), repintar solo
// esa celda alcanza para que la fila entera vuelva a medir "la más alta de
// las dos" -- CSS puro, sin medir nada a mano. Debounce propio (no el de
// _procAutoguardar: éste es solo visual, más corto) -- repintar en cada
// tecla volvería a disparar el fetch de cada imagen y el redibujado de
// cada gráfico/diagrama del documento entero.
let _procPreviewTimer = null;
// Botón "Ocultar/Mostrar vista previa" (pedido explícito) -- true = la
// grilla de 2 columnas de siempre; false = una sola columna, sin calcular
// nada de la vista previa (ver _procEditorHtml). Se reinicia a "visible"
// en cada _procRegistrar (documento nuevo = arranca mostrada otra vez).
let _procPreviewVisible = true;
function _procTogglePreview() {
  _procPreviewVisible = !_procPreviewVisible;
  _procRefrescarse();
}
// Botón "Alinear vista previa" (pedido explícito, SEPARADO del de arriba):
// false ("modo real") = el editor y la vista previa son dos columnas
// INDEPENDIENTES, cada una fluye a su altura natural -- así se ve de
// verdad, sin que un bloque corto de vista previa quede estirado por un
// formulario de edición más alto al lado. true ("alineada") = la grilla de
// 2 columnas de siempre, una fila de grilla por bloque, para comparar
// bloque a bloque a la misma altura (ver _procEditorHtml). No tiene efecto
// si la vista previa está oculta (_procPreviewVisible=false). Se reinicia
// a "modo real" en cada _procRegistrar, igual que _procPreviewVisible.
let _procPreviewAlineada = false;
function _procTogglePreviewAlineada() {
  _procPreviewAlineada = !_procPreviewAlineada;
  _procRefrescarse();
}
function _procActualizarPreview() {
  clearTimeout(_procPreviewTimer);
  _procPreviewTimer = null;
  if (!_procEditor || !_procPreviewVisible) return;
  _procPreviewTimer = setTimeout(() => {
    if (!_procEditor || !_procPreviewVisible) return;
    const bloquesLimpios = _procEditor.blocks.map(_procEditorLimpiarBloque);
    const anexos = _procAnexosDe(bloquesLimpios);
    bloquesLimpios.forEach((b, i) => {
      if (_procPreviewAlineada && b && b.type === 'pasos') {
        // Fila por fila (ver _procEditorBloquePasosAlineadoFilas) -- la
        // celda base solo tiene el título, cada paso tiene la suya aparte,
        // y con el bloque CRUDO (no bloquesLimpios[i]) para que la
        // correspondencia fila a fila no se rompa mientras se escribe.
        const crudo = _procEditor.blocks[i];
        _procRefrescar('proc-preview-fila-' + i, '<div class="card-title">' + _esc((crudo.data && crudo.data.titulo) || 'Pasos') + '</div>');
        _procRefrescarPasosAlineados(i, crudo.data && crudo.data.pasos, [], '', anexos);
      } else {
        _procRefrescar('proc-preview-fila-' + i, _procVistaPreviaBloqueHtml(b, anexos, i));
      }
    });
    _procCargarImagenesLocal(bloquesLimpios);
    _procDibujarGraficos({ blocks: bloquesLimpios });
    if (bloquesLimpios.some(b => b && b.type === 'diagrama')) _procProcesarDiagramas();
  }, 400);
}
// Igual que _procCargarImagenes, pero para la vista previa en vivo del
// editor: no puede usar _PROC_ARCHIVOS_ACTUALES (ver _procVistaPreviaBloqueHtml).
function _procCargarImagenesLocal(bloquesLimpios) {
  if (!_procActual || !_procActual.id) return;
  const pid = _procActual.id;
  for (const a of _procArchivosDeContenido(bloquesLimpios)) {
    if (_PROC_EXT_IMAGEN.has(a.ext)) _procCargarUnaImagen(pid, a.fileId);
  }
}
// Barra de acciones cuando el editor está CERRADO: "Modificar" (sin
// borrador en curso), retomar/descartar (hay uno 'editando' sin enviar) o
// el cartel de aprobación (hay uno 'pendiente_aprobacion'). Quien solo
// puede VER (no editar) solo ve el cartel de aprobación si existe -- nunca
// el botón de modificar ni el de "editando" (ver _procRenderPagina).
function _procHerramientasHtml(p) {
  if (p.borrador_estado === 'pendiente_aprobacion') return _procBannerBorradorHtml(p);
  if (p.borrador_estado === 'editando') {
    return `<div class="proc-anexo-form" style="margin-top:14px">
      <div class="proc-vacio" style="margin-bottom:8px">Ten&eacute;s un borrador sin enviar${p.borrador_por_nombre ? ' (de ' + _esc(p.borrador_por_nombre) + ')' : ''}.</div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button type="button" class="page-btn" onclick="_procAbrirEditor()">Continuar editando</button>
        <button type="button" class="page-btn" style="color:var(--red)" onclick="_procDescartarBorrador()">Descartar</button>
      </div>
    </div>`;
  }
  return `<div style="margin-top:14px"><button type="button" class="page-btn" onclick="_procAbrirEditor()">&#9998; Modificar</button></div>`;
}
// Cartel de "pendiente de aprobación" -- visible para CUALQUIERA que pueda
// ver el documento (transparencia: hay un cambio esperando), pero los
// botones de Aprobar/Rechazar/Descartar solo para quien puede editar, y
// Aprobar/Rechazar nunca para el propio autor (control de 4 ojos, ver
// post_procedimiento_borrador_aprobar en main.py). "Visualizar borrador"
// (ver _procAbrirBorradorModal) va al lado de los dos -- sin él, ni el
// autor ni quien tiene que aprobar podían ver el contenido nuevo antes de
// decidir, solo el motivo en texto. No pide nada al servidor: p.contenido
// ya viaja en la respuesta para quien puede editar (ver _proc_dict/
// incluir_borrador en main.py), que es justamente a quien se le ofrece acá.
function _procBannerBorradorHtml(p) {
  const puede = _procPuedeEditar();
  const esAutor = puede && _AUTH.user && p.borrador_por_legajo === _AUTH.user.legajo;
  return `<div class="proc-anexo-form" style="margin-top:14px;border-color:var(--yellow)">
    <div class="card-title">Pendiente de aprobaci&oacute;n</div>
    <div class="proc-vacio">Enviado por ${_esc(p.borrador_por_nombre || '')}${p.borrador_enviado_en ? ' el ' + fmtFechaCorta(p.borrador_enviado_en) : ''}.</div>
    ${p.borrador_motivo ? `<div class="proc-vacio"><b>Motivo:</b> ${_esc(p.borrador_motivo)}</div>` : ''}
    ${puede && !esAutor ? `<div style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap">
      <button type="button" class="page-btn" onclick="_procAprobarBorrador()">&#10003; Aprobar</button>
      <button type="button" class="page-btn" onclick="_procRechazarBorrador()">&#10007; Rechazar</button>
      <button type="button" class="page-btn" onclick="_procAbrirBorradorModal()">&#128065; Visualizar borrador</button>
    </div>` : ''}
    ${esAutor ? `<div class="proc-vacio" style="margin-top:8px">Lo mandaste vos -- hace falta otra persona con permiso para aprobarlo o rechazarlo.</div>
      <div style="margin-top:4px;display:flex;gap:8px;flex-wrap:wrap">
        <button type="button" class="page-btn" onclick="_procAbrirEditor()">Seguir editando</button>
        <button type="button" class="page-btn" style="color:var(--red)" onclick="_procDescartarBorrador()">Descartar</button>
        <button type="button" class="page-btn" onclick="_procAbrirBorradorModal()">&#128065; Visualizar borrador</button>
      </div>` : ''}
  </div>`;
}

// ── Editar el contenido principal (modo blog, por bloques) ─────────────────
// Control de versiones con aprobación (pedido explícito del usuario):
// "Modificar" arranca un BORRADOR en el servidor (POST .../borrador) que
// convive con lo publicado -- _procEditor es una copia LOCAL de ese
// borrador, con autoguardado propio (ver _procSucio/_procAutoguardar, más
// abajo) hacia PUT .../borrador. "Guardar" no guarda directo: pide el
// motivo del cambio y lo manda a .../borrador/enviar, quedando
// 'pendiente_aprobacion' hasta que OTRA persona con 'procedimientos_editar'
// lo apruebe (sube la revisión +1 y reemplaza lo publicado) o lo rechace
// (vuelve a 'editando' para corregir). Nunca un PUT directo del documento
// -- esa puerta está cerrada en el backend a propósito.
//
// Los anexos (nombre = palabra clave que _procEnlazarTexto linkea en el
// resto del texto) son UN tipo de bloque más acá (antes tenían su propio
// formulario aparte, "Agregar anexo" -- unificado por pedido del usuario:
// todo lo editable vive en un solo editor). La fila es un par [clave,valor]
// igual que 'ficha', así que reutiliza sus mismas funciones de edición
// (_procEditorFichaFila/Agregar/QuitarFila) -- lo único propio de 'anexo' es
// la validación de 'nombre' (obligatorio y único) al guardar, ver
// _procEditorGuardar.
//
// Bloques que este editor sabe crear/editar: header, paragraph, list, table,
// ficha, pasos, anexo, imagen, grafico. Los tipos sin Tool todavía
// (diagrama) se ven como "sin editor", mismo criterio que
// _procRenderBloque en lectura.
let _procEditor = null; // { titulo, blocks: [...] } (copia editable del borrador) o null si está cerrado
let _procBorradorModificadoEn = null; // optimistic lock del borrador, mientras _procEditor está abierto

const _PROC_BLOQUE_DEFAULT = {
  header: () => ({ text: '', level: 2 }),
  paragraph: () => ({ text: '' }),
  list: () => ({ style: 'unordered', items: [''] }),
  table: () => ({ content: [['', '']], borde: { grosor: _PROC_TABLA_BORDE_DEFAULT.grosor, color: _PROC_TABLA_BORDE_DEFAULT.color } }),
  ficha: () => ({ titulo: '', filas: [['', '']] }),
  pasos: () => ({ titulo: 'Pasos', pasos: [{ titulo: '', detalles: [''], nota: '', archivos: [], subpasos: [] }] }),
  anexo: () => ({ tipo: 'generico', nombre: '', filas: [['', '']], archivos: [], archivo: null, procedimientoCodigo: null, procedimientoTitulo: '' }),
  imagen: () => ({ fileId: null, nombre: '', ext: '' }),
  grafico: () => ({ tipo: 'bar', titulo: '', filas: [['', '']] }),
  diagrama: () => ({ titulo: '', xml: '' }),
};
const _PROC_BLOQUE_LABEL = { header: 'Encabezado', paragraph: 'Texto', list: 'Lista', table: 'Tabla', ficha: 'Ficha', pasos: 'Pasos', anexo: 'Anexo', imagen: 'Imagen / Archivo', grafico: 'Gráfico', diagrama: 'Diagrama' };
const _PROC_GRAFICO_TIPOS = [['bar', 'Barras'], ['line', 'Línea'], ['pie', 'Torta'], ['doughnut', 'Anillo'], ['radar', 'Radar']];

// Barra de "agregar bloque", agrupada por tipo de contenido en vez de la
// fila plana de 9 botones de texto de antes (pedido explícito: "estilo
// LibreOffice" -- grupos separados por una línea, ícono + etiqueta chica).
// El onclick de cada botón sigue siendo exactamente _procEditorAgregarBloque:
// esto es solo presentación, ver _procToolbarHtml más abajo.
const _PROC_TOOLBAR_GRUPOS = [
  { titulo: 'Texto', tipos: ['header', 'paragraph', 'list', 'table'] },
  { titulo: 'Datos', tipos: ['ficha', 'pasos'] },
  { titulo: 'Archivos', tipos: ['imagen', 'anexo', 'grafico'] },
  // Grupo propio (no "Archivos"): a diferencia de los demás, este bloque no
  // se edita inline con un formulario -- abre un editor aparte (ver
  // _procDiagramaEditorAbrir), más parecido a un programa externo que a un
  // campo más del documento.
  { titulo: 'Diagrama', tipos: ['diagrama'] },
];
const _PROC_BLOQUE_ICONO = {
  header: 'H', paragraph: '&#182;', list: '&#8226;', table: '&#9638;',
  ficha: '&#128450;', pasos: '&#128290;',
  imagen: '&#128444;', anexo: '&#128206;', grafico: '&#128202;',
  diagrama: '&#128256;',
};
// _PROC_BLOQUE_LABEL dice "Imagen / Archivo" -- bien para el encabezado de
// un bloque ya puesto, demasiado largo para la etiqueta chica del ícono.
const _PROC_TOOLBAR_ETIQUETA = { imagen: 'Imagen' };
function _procToolbarHtml() {
  const grupos = _PROC_TOOLBAR_GRUPOS.map(g => `<div class="proc-toolbar-grupo">
    <div class="proc-toolbar-grupo-titulo">${_esc(g.titulo)}</div>
    <div class="proc-toolbar-botones">
      ${g.tipos.map(t => `<button type="button" class="proc-toolbar-btn" title="${_esc(_PROC_BLOQUE_LABEL[t])}" onclick="_procEditorAgregarBloque('${t}')">
        <span class="proc-toolbar-ico">${_PROC_BLOQUE_ICONO[t]}</span><span class="proc-toolbar-lbl">${_esc(_PROC_TOOLBAR_ETIQUETA[t] || _PROC_BLOQUE_LABEL[t])}</span>
      </button>`).join('')}
    </div>
  </div>`).join('');
  // Grupo "Vista" (pedido explícito: estos dos botones a la barra de
  // herramientas, a la derecha) -- a diferencia de los de arriba, no
  // agregan un bloque: conmutan cómo se ve la vista previa en vivo que ya
  // está (ver _procTogglePreview/_procTogglePreviewAlineada). margin-left:
  // auto los empuja al extremo derecho porque .proc-toolbar es flex.
  const vista = `<div class="proc-toolbar-grupo" style="margin-left:auto">
    <div class="proc-toolbar-grupo-titulo">Vista</div>
    <div class="proc-toolbar-botones">
      <button type="button" class="proc-toolbar-btn" title="${_procPreviewVisible ? 'Ocultar vista previa' : 'Mostrar vista previa'}" onclick="_procTogglePreview()">
        <span class="proc-toolbar-ico">&#128065;</span><span class="proc-toolbar-lbl">${_procPreviewVisible ? 'Ocultar' : 'Mostrar'}</span>
      </button>
      ${_procPreviewVisible ? `<button type="button" class="proc-toolbar-btn" title="${_procPreviewAlineada ? 'Vista previa en modo real' : 'Alinear vista previa'}" onclick="_procTogglePreviewAlineada()">
        <span class="proc-toolbar-ico">&#128208;</span><span class="proc-toolbar-lbl">${_procPreviewAlineada ? 'Modo real' : 'Alinear'}</span>
      </button>` : ''}
    </div>
  </div>`;
  return `<div class="proc-toolbar">${grupos}${vista}</div>`;
}
// Los tres "sabores" de anexo (ver el caso 'anexo' de _procEditorCamposHtml,
// _procBloqueAnexo y _procAbrirAnexo): 'generico' es la ficha + archivos de
// siempre; 'archivo' es un anexo que ES un solo archivo (reusa el mismo
// _procArchivoHtml del bloque 'imagen'); 'procedimiento' apunta a OTRO
// procedimiento del sistema y, al tocar su palabra clave, navega ahí en vez
// de abrir la ventana chica (con go(), para que las flechas de
// navegación del programa puedan volver).
const _PROC_ANEXO_TIPOS = [['generico', 'Gen&eacute;rico (ficha)'], ['archivo', 'Archivo (imagen o adjunto)'], ['procedimiento', 'Otro procedimiento']];
// Catálogo de procedimientos para el selector del tipo 'procedimiento' --
// se carga una sola vez (null = todavía no), bajo demanda (cuando alguien
// elige ese tipo, ver _procEditorAnexoTipo) y nunca durante un render común,
// para no disparar un fetch de la nada en cualquier otra prueba/pantalla
// que solo esté pintando bloques.
let _PROC_LISTA_PARA_ANEXO = null;
async function _procCargarListaParaAnexo() {
  if (_PROC_LISTA_PARA_ANEXO) return;
  try { _PROC_LISTA_PARA_ANEXO = await api('/api/procedimientos'); } catch (e) { _PROC_LISTA_PARA_ANEXO = []; }
  _procRefrescarse();
}

// "Modificar": arranca (o retoma, si ya había uno 'editando') el borrador
// en el servidor. 409 si ya hay uno 'pendiente_aprobacion' -- hay que
// resolverlo antes (ver _procBannerBorradorHtml).
async function _procAbrirEditor() {
  if (_procTourActivo) return; // el recorrido arma el editor a mano, nunca por acá (ver _procTourSincronizar)
  if (!_procPuedeEditar() || !_procActual || _procActual.error) return;
  try {
    const actualizado = await api('/api/procedimientos/' + _procActual.id + '/borrador', { method: 'POST' });
    _procActual = actualizado;
    if (_procOnActualizar) _procOnActualizar(actualizado);
    _procEditor = { titulo: actualizado.borrador_titulo || '', blocks: JSON.parse(JSON.stringify((actualizado.borrador_contenido && actualizado.borrador_contenido.blocks) || [])) };
    _procBorradorModificadoEn = actualizado.borrador_modificado_en;
    document.body.classList.add('proc-full'); // al entrar en modo edición, automático -- el botón de _procEditorHtml lo puede volver a mostrar
    _procRefrescarse();
  } catch (e) {
    alert('No se pudo empezar a modificar: ' + _procMsgError(e));
  }
}
// "Cerrar": solo oculta el panel -- el borrador queda guardado en el
// servidor (autoguardado), se puede retomar después con "Continuar
// editando" (ver _procHerramientasHtml). No pide confirmación: no se
// pierde nada. Para tirar el borrador entero, ver _procDescartarBorrador.
function _procCerrarEditor() {
  clearTimeout(_procAutoguardadoTimer);
  clearTimeout(_procPreviewTimer);
  document.body.classList.remove('proc-full'); // no dejar el menú escondido fuera del editor, sin que se note por qué
  _procEditor = null;
  _procBorradorModificadoEn = null;
  _procRefrescarse();
}
// Esconder/mostrar el menú lateral (barra de pestañas principal) para
// ganar ancho en la pantalla dividida -- pedido explícito del usuario. Solo
// toca una clase de <body> (la regla vive en _procEstilos) y repinta para
// que el botón cambie de "Ocultar" a "Mostrar" (ver _procEditorHtml).
function _procToggleSidebar() {
  document.body.classList.toggle('proc-full');
  _procRefrescarse();
}
function _procEditorTitulo(valor) {
  if (!_procEditor) return;
  _procEditor.titulo = valor;
  _procSucio(false);
}
function _procEditorMover(i, delta) {
  if (!_procEditor) return;
  const j = i + delta;
  if (j < 0 || j >= _procEditor.blocks.length) return;
  const b = _procEditor.blocks;
  [b[i], b[j]] = [b[j], b[i]];
  _procSucio(true);
  _procRefrescarse();
}
function _procEditorEliminarBloque(i) {
  if (!_procEditor || !_procEditor.blocks[i]) return;
  _procEditor.blocks.splice(i, 1);
  _procSucio(true);
  _procRefrescarse();
}
function _procEditorAgregarBloque(tipo) {
  if (!_procEditor || !_PROC_BLOQUE_DEFAULT[tipo]) return;
  if (_procEditor.blocks.length >= 200) { alert('Máximo 200 bloques por procedimiento.'); return; }
  _procEditor.blocks.push({ type: tipo, data: _PROC_BLOQUE_DEFAULT[tipo]() });
  _procSucio(true);
  _procRefrescarse();
}
// Setter genérico para campos de texto simples (header.text, paragraph.text,
// ficha.titulo, pasos.titulo) -- los que tienen forma propia (listas,
// filas, pasos anidados) tienen su propia función más abajo.
function _procEditorCampo(i, campo, valor) {
  if (!_procEditor || !_procEditor.blocks[i]) return;
  _procEditor.blocks[i].data[campo] = valor;
  _procSucio(false);
}
function _procEditorNivel(i, valor) {
  if (!_procEditor || !_procEditor.blocks[i]) return;
  _procEditor.blocks[i].data.level = Number(valor) || 2;
  _procSucio(false);
}
function _procEditorListaEstilo(i, valor) {
  if (!_procEditor || !_procEditor.blocks[i]) return;
  _procEditor.blocks[i].data.style = valor;
  _procSucio(false);
}
function _procEditorListaItems(i, valor) {
  if (!_procEditor || !_procEditor.blocks[i]) return;
  _procEditor.blocks[i].data.items = valor.split('\n');
  _procSucio(false);
}
// Tabla tipo Excel: todas las filas tienen el mismo largo (agregar/quitar
// columna opera sobre TODAS a la vez) -- a diferencia de ficha/pasos, no
// hace falta limpiar nada al guardar (no hay renglon "vacio" posible, una
// celda vacia es un dato valido en una tabla).
function _procEditorTablaCelda(i, fila, col, valor) {
  if (!_procEditor || !_procEditor.blocks[i]) return;
  _procEditor.blocks[i].data.content[fila][col] = valor;
  _procSucio(false);
}
function _procEditorTablaAgregarFila(i) {
  if (!_procEditor || !_procEditor.blocks[i]) return;
  const d = _procEditor.blocks[i].data;
  const contenido = d.content;
  if (contenido.length >= 100) { alert('Máximo 100 filas por tabla.'); return; }
  const cols = (contenido[0] || []).length || 1;
  contenido.push(new Array(cols).fill(''));
  if (d.filas_fondo) d.filas_fondo.push(''); // mantiene filas_fondo del mismo largo que content, misma idea que cols en cada fila
  _procSucio(true);
  _procRefrescarse();
}
function _procEditorTablaQuitarFila(i, fila) {
  if (!_procEditor || !_procEditor.blocks[i]) return;
  const d = _procEditor.blocks[i].data;
  const contenido = d.content;
  if (contenido.length <= 1) return; // siempre queda al menos una fila
  contenido.splice(fila, 1);
  if (d.filas_fondo) d.filas_fondo.splice(fila, 1); // la fila que se va se lleva su color -- quedan alineados por índice
  _procSucio(true);
  _procRefrescarse();
}
function _procEditorTablaAgregarColumna(i) {
  if (!_procEditor || !_procEditor.blocks[i]) return;
  const contenido = _procEditor.blocks[i].data.content;
  if ((contenido[0] || []).length >= 20) { alert('Máximo 20 columnas por tabla.'); return; }
  contenido.forEach(fila => fila.push(''));
  _procSucio(true);
  _procRefrescarse();
}
function _procEditorTablaQuitarColumna(i, col) {
  if (!_procEditor || !_procEditor.blocks[i]) return;
  const contenido = _procEditor.blocks[i].data.content;
  if ((contenido[0] || []).length <= 1) return; // siempre queda al menos una columna
  contenido.forEach(fila => fila.splice(col, 1));
  _procSucio(true);
  _procRefrescarse();
}
// Borde de TODA la tabla (grosor en px, 0 a 5 -- 0 es "sin borde") y color
// de fondo por FILA: ver el comentario grande de _procBloqueTable para el
// porqué de este grano (tabla entera / fila, no celda por celda). 'valor'
// siempre viene de un <select>/<input type=color> del propio formulario,
// pero se sanea igual -- mismo criterio defensivo que el resto del archivo
// (ej. _procEditorNivel con Number(valor)||2).
function _procEditorTablaBorde(i, campo, valor) {
  if (!_procEditor || !_procEditor.blocks[i]) return;
  const d = _procEditor.blocks[i].data;
  const actual = _procTablaBordeSano(d.borde);
  if (campo === 'grosor') actual.grosor = Math.min(Math.max(parseInt(valor, 10) || 0, 0), 5);
  else actual.color = _procColorValido(valor) || actual.color; // un color invalido no pisa el que ya habia
  d.borde = actual;
  _procSucio(true);
  _procRefrescarse();
}
// '' quita el color (vuelve a "sin fondo", no un string roto) -- lo manda el
// botón "Quitar color" del editor. filas_fondo se crea recién al primer uso
// para no ensuciar con un array de vacíos cada tabla que nunca lo toca.
function _procEditorTablaFilaFondo(i, fila, valor) {
  if (!_procEditor || !_procEditor.blocks[i]) return;
  const d = _procEditor.blocks[i].data;
  if (!d.filas_fondo) d.filas_fondo = [];
  d.filas_fondo[fila] = valor === '' ? '' : (_procColorValido(valor) || '');
  _procSucio(true);
  _procRefrescarse();
}
// Las filas de ficha se guardan como pares [clave, valor] (no {clave,valor}
// como el formulario de anexo) -- k es 0 o 1.
function _procEditorFichaFila(i, j, k, valor) {
  if (!_procEditor || !_procEditor.blocks[i] || !_procEditor.blocks[i].data.filas[j]) return;
  _procEditor.blocks[i].data.filas[j][k] = valor;
  _procSucio(false);
}
function _procEditorFichaAgregarFila(i) {
  if (!_procEditor || !_procEditor.blocks[i]) return;
  if (_procEditor.blocks[i].data.filas.length >= 50) { alert('Máximo 50 filas.'); return; }
  _procEditor.blocks[i].data.filas.push(['', '']);
  _procSucio(true);
  _procRefrescarse();
}
function _procEditorFichaQuitarFila(i, j) {
  if (!_procEditor || !_procEditor.blocks[i]) return;
  _procEditor.blocks[i].data.filas.splice(j, 1);
  _procSucio(true);
  _procRefrescarse();
}
// 'ruta' direcciona un paso dentro de data.pasos sin importar el nivel:
// [j] = paso de nivel 1, [j,k] = subpaso (se numera "5.1" en lectura),
// [j,k,l] = subsubpaso ("5.1.1") -- pedido explícito, siempre hasta 3
// niveles, nunca "5.1.1.1" (ver el tope en _procEditorCamposHtml, caso
// 'pasos'). _procPasoPadreYPos resuelve el ARREGLO que lo contiene (para
// poder empujar/sacar/mover) más su posición ahí adentro; _procPasoEn
// devuelve directamente el paso.
function _procPasoPadreYPos(bloque, ruta) {
  let arreglo = bloque.data.pasos;
  for (let n = 0; n < ruta.length - 1; n++) {
    const p = arreglo && arreglo[ruta[n]];
    if (!p) return null;
    if (!p.subpasos) p.subpasos = [];
    arreglo = p.subpasos;
  }
  return arreglo ? { arreglo, pos: ruta[ruta.length - 1] } : null;
}
function _procPasoEn(bloque, ruta) {
  const r = _procPasoPadreYPos(bloque, ruta);
  return r ? r.arreglo[r.pos] : undefined;
}
function _procEditorPasoCampo(i, ruta, campo, valor) {
  const b = _procEditor && _procEditor.blocks[i];
  const p = b && _procPasoEn(b, ruta);
  if (!p) return;
  p[campo] = valor;
  _procSucio(false);
}
function _procEditorPasoDetalles(i, ruta, valor) {
  const b = _procEditor && _procEditor.blocks[i];
  const p = b && _procPasoEn(b, ruta);
  if (!p) return;
  p.detalles = valor.split('\n');
  _procSucio(false);
}
// rutaPadre vacía (o ausente) agrega un paso de nivel 1 (a data.pasos);
// [j] agrega un SUBpaso del paso j; [j,k] agrega un subSUBpaso de ese
// subpaso. Nunca más profundo que eso -- el botón para llegar más hondo
// directamente no existe (ver el tope de 3 niveles en _procEditorCamposHtml).
function _procEditorPasoAgregar(i, rutaPadre) {
  if (!_procEditor || !_procEditor.blocks[i]) return;
  const bloque = _procEditor.blocks[i];
  let arreglo;
  if (rutaPadre && rutaPadre.length) {
    const padre = _procPasoEn(bloque, rutaPadre);
    if (!padre) return;
    if (!padre.subpasos) padre.subpasos = [];
    arreglo = padre.subpasos;
  } else {
    arreglo = bloque.data.pasos;
  }
  if (arreglo.length >= 50) { alert('Máximo 50 pasos en este nivel.'); return; }
  arreglo.push({ titulo: '', detalles: [''], nota: '', archivos: [], subpasos: [] });
  _procSucio(true);
  _procRefrescarse();
}
function _procEditorPasoQuitar(i, ruta) {
  const b = _procEditor && _procEditor.blocks[i];
  if (!b) return;
  const r = _procPasoPadreYPos(b, ruta);
  if (!r) return;
  const paso = r.arreglo[r.pos];
  // Splice se lleva puesto todo lo que cuelgue de este paso -- avisar antes
  // si hay algo que perder, mismo criterio que el resto de los confirm() de
  // este archivo (aprobar/rechazar/descartar borrador, eliminar documento).
  const perdidos = paso ? _procTodosLosPasos(paso.subpasos).length : 0;
  if (perdidos > 0 && !confirm('Este paso tiene ' + perdidos + ' subpaso(s). ¿Eliminarlo igual? Se eliminan también todos sus subpasos.')) return;
  r.arreglo.splice(r.pos, 1);
  _procSucio(true);
  _procRefrescarse();
}
// Reordena un paso DENTRO de su propio padre (mismo nivel, nunca cambia de
// padre) -- distinto de _procEditorMover, que reordena bloques enteros.
function _procEditorPasoMover(i, ruta, delta) {
  const b = _procEditor && _procEditor.blocks[i];
  if (!b) return;
  const r = _procPasoPadreYPos(b, ruta);
  if (!r) return;
  const k = r.pos + delta;
  if (k < 0 || k >= r.arreglo.length) return;
  [r.arreglo[r.pos], r.arreglo[k]] = [r.arreglo[k], r.arreglo[r.pos]];
  _procSucio(true);
  _procRefrescarse();
}

// Subida de archivos -- UN solo <input type=file> oculto, compartido por
// TODOS los bloques que suben algo (el bloque 'imagen', los 'archivos' de un
// anexo genérico, el archivo único de un anexo tipo 'archivo', o un paso
// dentro de un bloque 'pasos'), igual que #sn-foto-input en sinterizado.js:
// _procImgBloqueActual (+ _procImgPasoRuta, solo en modo 'paso') guarda a
// dónde va el archivo elegido, y _procImgModo si hay que REEMPLAZAR el data
// del bloque ('imagen' o 'anexo-archivo-unico') o AGREGAR a una lista
// ('anexo'/'paso' -- ambos pueden llevar varios). fetch+FormData directo (no
// la función api(), que es para JSON) -- mismo patrón que _snFotoArchivoElegido.
let _procImgBloqueActual = null;
let _procImgPasoRuta = null; // ruta (ver _procPasoEn) del paso/subpaso/subsubpaso -- solo en modo 'paso'
let _procImgModo = 'imagen';
function _procEditorImagenElegir(i) {
  if (!_procEditor || !_procEditor.blocks[i]) return;
  _procImgBloqueActual = i;
  _procImgModo = 'imagen';
  const inp = $id('proc-img-input');
  if (inp) { inp.value = ''; inp.click(); }
}
function _procEditorAnexoArchivoElegir(i) {
  if (!_procEditor || !_procEditor.blocks[i]) return;
  _procImgBloqueActual = i;
  _procImgModo = 'anexo';
  const inp = $id('proc-img-input');
  if (inp) { inp.value = ''; inp.click(); }
}
function _procEditorAnexoArchivoUnicoElegir(i) {
  if (!_procEditor || !_procEditor.blocks[i]) return;
  _procImgBloqueActual = i;
  _procImgModo = 'anexo-archivo-unico';
  const inp = $id('proc-img-input');
  if (inp) { inp.value = ''; inp.click(); }
}
function _procEditorAnexoArchivoUnicoQuitar(i) {
  if (!_procEditor || !_procEditor.blocks[i]) return;
  _procEditor.blocks[i].data.archivo = null;
  _procSucio(true);
  _procRefrescarse();
}
// Cambiar el tipo de anexo (generico/archivo/procedimiento) -- dispara la
// carga del catálogo de procedimientos recién la PRIMERA vez que alguien
// elige 'procedimiento' (nunca durante un render común, ver
// _PROC_LISTA_PARA_ANEXO).
function _procEditorAnexoTipo(i, valor) {
  if (!_procEditor || !_procEditor.blocks[i]) return;
  _procEditor.blocks[i].data.tipo = valor;
  if (valor === 'procedimiento' && _PROC_LISTA_PARA_ANEXO === null) _procCargarListaParaAnexo();
  _procSucio(true);
  _procRefrescarse();
}
function _procEditorAnexoProcedimiento(i, codigo) {
  if (!_procEditor || !_procEditor.blocks[i]) return;
  const p = (_PROC_LISTA_PARA_ANEXO || []).find(x => x.codigo === codigo);
  _procEditor.blocks[i].data.procedimientoCodigo = codigo || null;
  _procEditor.blocks[i].data.procedimientoTitulo = p ? p.titulo : '';
  _procSucio(false);
}
function _procEditorPasoArchivoElegir(i, ruta) {
  const b = _procEditor && _procEditor.blocks[i];
  if (!b || !_procPasoEn(b, ruta)) return;
  _procImgBloqueActual = i;
  _procImgPasoRuta = ruta;
  _procImgModo = 'paso';
  const inp = $id('proc-img-input');
  if (inp) { inp.value = ''; inp.click(); }
}
async function _procEditorImagenArchivoElegido(inp) {
  const file = inp.files && inp.files[0];
  const i = _procImgBloqueActual;
  const ruta = _procImgPasoRuta;
  const modo = _procImgModo;
  if (!file || i == null || !_procEditor || !_procEditor.blocks[i]) return;
  if (modo === 'paso' && (!ruta || !_procPasoEn(_procEditor.blocks[i], ruta))) return;
  const fd = new FormData();
  fd.append('file', file, file.name || 'archivo');
  try {
    const resp = await fetch('/api/procedimientos/' + _procActual.id + '/imagenes', {
      method: 'POST', headers: { Authorization: 'Bearer ' + _AUTH.token }, body: fd,
    });
    if (!resp.ok) throw new Error(await resp.text());
    const subido = await resp.json();
    if (!_procEditor || !_procEditor.blocks[i]) return; // se pudo haber cerrado/sacado el bloque mientras subía
    const archivo = { fileId: subido.fileId, nombre: subido.nombre || file.name, ext: subido.ext };
    if (modo === 'anexo') {
      if (!_procEditor.blocks[i].data.archivos) _procEditor.blocks[i].data.archivos = [];
      if (_procEditor.blocks[i].data.archivos.length >= 20) { alert('Máximo 20 archivos por anexo.'); return; }
      _procEditor.blocks[i].data.archivos.push(archivo);
    } else if (modo === 'paso') {
      const paso = _procPasoEn(_procEditor.blocks[i], ruta);
      if (!paso) return;
      if (!paso.archivos) paso.archivos = [];
      if (paso.archivos.length >= 20) { alert('Máximo 20 archivos por paso.'); return; }
      paso.archivos.push(archivo);
    } else if (modo === 'anexo-archivo-unico') {
      _procEditor.blocks[i].data.archivo = archivo;
    } else {
      _procEditor.blocks[i].data = archivo;
    }
    _procSucio(true);
    _procRefrescarse();
  } catch (e) {
    alert('No se pudo subir el archivo: ' + _procMsgError(e));
  }
}
function _procEditorImagenQuitar(i) {
  if (!_procEditor || !_procEditor.blocks[i]) return;
  _procEditor.blocks[i].data = { fileId: null, nombre: '', ext: '' };
  _procSucio(true);
  _procRefrescarse();
}
function _procEditorAnexoArchivoQuitar(i, k) {
  if (!_procEditor || !_procEditor.blocks[i] || !_procEditor.blocks[i].data.archivos) return;
  _procEditor.blocks[i].data.archivos.splice(k, 1);
  _procSucio(true);
  _procRefrescarse();
}
// Reordena los archivos DENTRO de un anexo genérico -- distinto de
// _procEditorMover (bloques) y _procEditorPasoMover (pasos).
function _procEditorAnexoArchivoMover(i, k, delta) {
  if (!_procEditor || !_procEditor.blocks[i] || !_procEditor.blocks[i].data.archivos) return;
  const archivos = _procEditor.blocks[i].data.archivos;
  const m = k + delta;
  if (m < 0 || m >= archivos.length) return;
  [archivos[k], archivos[m]] = [archivos[m], archivos[k]];
  _procSucio(true);
  _procRefrescarse();
}
function _procEditorPasoArchivoQuitar(i, ruta, k) {
  const b = _procEditor && _procEditor.blocks[i];
  const paso = b && _procPasoEn(b, ruta);
  if (!paso || !paso.archivos) return;
  paso.archivos.splice(k, 1);
  _procSucio(true);
  _procRefrescarse();
}
// Reordena los archivos DENTRO de un paso -- mismo criterio.
function _procEditorPasoArchivoMover(i, ruta, k, delta) {
  const b = _procEditor && _procEditor.blocks[i];
  const paso = b && _procPasoEn(b, ruta);
  if (!paso || !paso.archivos) return;
  const m = k + delta;
  if (m < 0 || m >= paso.archivos.length) return;
  [paso.archivos[k], paso.archivos[m]] = [paso.archivos[m], paso.archivos[k]];
  _procSucio(true);
  _procRefrescarse();
}

// Un paso del editor (y, recursivamente, sus subpasos/subsubpasos). 'ruta'
// lo direcciona igual que en _procPasoPadreYPos/_procPasoEn -- se escribe
// como literal de arreglo ([${ruta}]) porque Array#toString ya junta sus
// elementos con comas, que es exactamente lo que va entre [ ] en JS. El
// tope de 3 niveles se aplica ACÁ, no en _procPasoAgregar: a nivel 3
// (subsubpaso) no se ofrece "Agregar subpaso" porque _procPasoHtml ya no
// numera un nivel más (ver el comentario de _procPasoPadreYPos).
//
// 'incluirHijos' (default true): false cuando cada subpaso ya va a ser su
// PROPIA fila de grilla aparte (modo "alineada", ver
// _procEditorBloquePasosAlineadoFilas) -- ahí pintarlos ACÁ ADEMÁS los
// duplicaría. En todo el resto de los casos (formulario normal, "modo
// real") se siguen anidando como siempre.
function _procPasoEditorHtml(i, ruta, p, numero, nivel, totalHermanos, pos, incluirHijos) {
  if (incluirHijos === undefined) incluirHijos = true;
  const totalArchivos = (p.archivos || []).length;
  const archivos = (p.archivos || []).map((a, k) => `<div class="pv-row" style="margin-bottom:4px;align-items:center">
    <span style="flex:1;font-size:12px">${_PROC_EXT_IMAGEN.has((a.ext || '').toLowerCase()) ? '&#128247;' : '&#128196;'} ${_esc(a.nombre || 'archivo')}</span>
    <button type="button" class="page-btn" style="padding:2px 6px" ${k === 0 ? 'disabled' : ''} onclick="_procEditorPasoArchivoMover(${i},[${ruta}],${k},-1)" title="Subir">&#9650;</button>
    <button type="button" class="page-btn" style="padding:2px 6px" ${k === totalArchivos - 1 ? 'disabled' : ''} onclick="_procEditorPasoArchivoMover(${i},[${ruta}],${k},1)" title="Bajar">&#9660;</button>
    <span class="sn-quitar" title="Quitar archivo" onclick="_procEditorPasoArchivoQuitar(${i},[${ruta}],${k})">&times;</span>
  </div>`).join('');
  const etiqueta = nivel === 1 ? 'Paso' : (nivel === 2 ? 'Subpaso' : 'Subsubpaso');
  const hijos = incluirHijos ? (p.subpasos || []).map((sp, k) => _procPasoEditorHtml(i, ruta.concat([k]), sp, numero + '.' + (k + 1), nivel + 1, (p.subpasos || []).length, k)).join('') : '';
  return `<div class="proc-step proc-step-nivel-${nivel}" style="margin-bottom:6px">
    <div style="display:flex;gap:4px;align-items:center;margin-bottom:4px">
      <span style="font-size:11px;color:var(--muted);flex:1">${etiqueta} ${numero}</span>
      <button type="button" class="page-btn" style="padding:2px 8px" ${pos === 0 ? 'disabled' : ''} onclick="_procEditorPasoMover(${i},[${ruta}],-1)" title="Subir">&#9650;</button>
      <button type="button" class="page-btn" style="padding:2px 8px" ${pos === totalHermanos - 1 ? 'disabled' : ''} onclick="_procEditorPasoMover(${i},[${ruta}],1)" title="Bajar">&#9660;</button>
    </div>
    <input class="inp" type="text" placeholder="T&iacute;tulo del paso" style="width:100%;margin-bottom:4px" value="${_esc(p.titulo)}" oninput="_procEditorPasoCampo(${i},[${ruta}],'titulo',this.value)">
    <textarea class="inp" style="width:100%;min-height:50px" placeholder="Un detalle por rengl&oacute;n" oninput="_procEditorPasoDetalles(${i},[${ruta}], this.value)">${_esc((p.detalles || []).join('\n'))}</textarea>
    <input class="inp" type="text" placeholder="Nota (opcional)" style="width:100%;margin-top:4px" value="${_esc(p.nota || '')}" oninput="_procEditorPasoCampo(${i},[${ruta}],'nota',this.value)">
    <div style="margin-top:6px">${archivos}</div>
    <div style="margin-top:4px;display:flex;gap:8px;flex-wrap:wrap;align-items:center">
      <button type="button" class="page-btn" onclick="_procEditorPasoArchivoElegir(${i},[${ruta}])">&#128247; Agregar imagen/archivo</button>
      ${nivel < 3 ? `<button type="button" class="page-btn" onclick="_procEditorPasoAgregar(${i},[${ruta}])">&#10133; Agregar ${nivel === 1 ? 'subpaso' : 'subsubpaso'}</button>` : ''}
      <span class="sn-quitar" title="Quitar ${etiqueta.toLowerCase()}" onclick="_procEditorPasoQuitar(${i},[${ruta}])">&times; Quitar ${etiqueta.toLowerCase()}</span>
    </div>
    ${hijos ? `<div class="proc-substeps">${hijos}</div>` : ''}
  </div>`;
}
function _procEditorBloqueHtml(b, i) {
  const total = _procEditor.blocks.length;
  const controles = `<div style="display:flex;gap:4px;align-items:center;margin-bottom:6px">
    <span style="font-size:11px;color:var(--muted);flex:1">${_esc(_PROC_BLOQUE_LABEL[b.type] || b.type)}</span>
    <button type="button" class="page-btn" style="padding:2px 8px" ${i === 0 ? 'disabled' : ''} onclick="_procEditorMover(${i},-1)" title="Subir">&#9650;</button>
    <button type="button" class="page-btn" style="padding:2px 8px" ${i === total - 1 ? 'disabled' : ''} onclick="_procEditorMover(${i},1)" title="Bajar">&#9660;</button>
    <span class="sn-quitar" title="Eliminar bloque" onclick="_procEditorEliminarBloque(${i})">&times;</span>
  </div>`;
  return `<div class="proc-anexo-form" style="margin-bottom:10px">${controles}${_procEditorCamposHtml(b, i)}</div>`;
}
// Fila por fila, para el modo "alineada" de un bloque 'pasos' (pedido
// explícito: que el botón "Agregar subpaso" del paso 1 quede a la altura
// de "1.1" en la vista previa, no a la del bloque 'pasos' entero). Se arma
// una fila de grilla APARTE por cada paso/subpaso/subsubpaso, en preorden
// (el padre primero y sus hijos justo después) -- mismos ids que usa
// _procRefrescarPasosAlineados para repintar en vivo. 'ruta'/'numero' son
// los del PADRE (arranca en [] / '' para los pasos de primer nivel).
function _procPasosAlineadosFilas(i, pasos, ruta, numero, anexos) {
  const nivel = ruta.length + 1;
  return (pasos || []).map((p, k) => {
    const rutaPaso = ruta.concat([k]);
    const numeroPaso = numero ? numero + '.' + (k + 1) : String(k + 1);
    const sangria = ruta.length * 16;
    const idPreview = 'proc-preview-fila-' + i + '-' + rutaPaso.join('-');
    const filaEditor = `<div class="proc-fila-editor" style="margin-left:${sangria}px">${_procPasoEditorHtml(i, rutaPaso, p, numeroPaso, nivel, (pasos || []).length, k, false)}</div>`;
    const filaPreview = `<div class="proc-fila-preview" id="${idPreview}" style="margin-left:${sangria}px">${_procPasoHtmlPropio(p, numeroPaso, anexos, nivel)}</div>`;
    return filaEditor + filaPreview + _procPasosAlineadosFilas(i, p.subpasos, rutaPaso, numeroPaso, anexos);
  }).join('');
}
// Repinta en vivo (debounce de cada tecla, ver _procActualizarPreview) la
// celda de vista previa de CADA paso/subpaso/subsubpaso cuando el bloque
// está en modo "alineada" -- mismos ids que _procPasosAlineadosFilas.
function _procRefrescarPasosAlineados(i, pasos, ruta, numero, anexos) {
  (pasos || []).forEach((p, k) => {
    const rutaPaso = ruta.concat([k]);
    const numeroPaso = numero ? numero + '.' + (k + 1) : String(k + 1);
    _procRefrescar('proc-preview-fila-' + i + '-' + rutaPaso.join('-'), _procPasoHtmlPropio(p, numeroPaso, anexos, rutaPaso.length));
    _procRefrescarPasosAlineados(i, p.subpasos, rutaPaso, numeroPaso, anexos);
  });
}
// Encabezado del bloque 'pasos' en modo alineada (controles de
// mover/eliminar el bloque + título + "Agregar paso", mismos que
// _procEditorBloqueHtml) MÁS una fila por cada paso -- el resto de los
// tipos de bloque siguen con UNA sola fila para todo el bloque entero (ver
// _procEditorHtml).
function _procEditorBloquePasosAlineadoFilas(b, i, anexos) {
  const d = b.data || {};
  const total = _procEditor.blocks.length;
  const controles = `<div style="display:flex;gap:4px;align-items:center;margin-bottom:6px">
    <span style="font-size:11px;color:var(--muted);flex:1">${_esc(_PROC_BLOQUE_LABEL[b.type] || b.type)}</span>
    <button type="button" class="page-btn" style="padding:2px 8px" ${i === 0 ? 'disabled' : ''} onclick="_procEditorMover(${i},-1)" title="Subir">&#9650;</button>
    <button type="button" class="page-btn" style="padding:2px 8px" ${i === total - 1 ? 'disabled' : ''} onclick="_procEditorMover(${i},1)" title="Bajar">&#9660;</button>
    <span class="sn-quitar" title="Eliminar bloque" onclick="_procEditorEliminarBloque(${i})">&times;</span>
  </div>`;
  const encabezadoEditor = `<div class="proc-fila-editor"><div class="proc-anexo-form" style="margin-bottom:10px">${controles}
    <input class="inp" type="text" placeholder="T&iacute;tulo del bloque" style="width:100%" value="${_esc(d.titulo || '')}" oninput="_procEditorCampo(${i},'titulo',this.value)">
    <button type="button" class="page-btn" style="margin-top:8px" onclick="_procEditorPasoAgregar(${i})">&#10133; Agregar paso</button>
  </div></div>`;
  const encabezadoPreview = `<div class="proc-fila-preview" id="proc-preview-fila-${i}"><div class="card-title">${_esc(d.titulo || 'Pasos')}</div></div>`;
  return encabezadoEditor + encabezadoPreview + _procPasosAlineadosFilas(i, d.pasos, [], '', anexos);
}
function _procEditorCamposHtml(b, i) {
  const d = b.data || {};
  if (b.type === 'header') {
    // .proc-campo-box: mismo panel con fondo que ya usaba 'pasos' (.proc-step)
    // -- antes este control quedaba suelto, sin ningún panel propio dentro
    // del .proc-anexo-form que envuelve TODO bloque (ver _procEditorBloqueHtml).
    return `<div class="proc-campo-box"><select class="inp" style="width:90px;margin-bottom:6px" onchange="_procEditorNivel(${i}, this.value)">
      ${[1, 2, 3, 4].map(n => `<option value="${n}" ${Number(d.level) === n ? 'selected' : ''}>H${n}</option>`).join('')}
    </select>
    <input class="inp" type="text" style="width:100%" placeholder="Texto del encabezado" value="${_esc(d.text)}" oninput="_procEditorCampo(${i},'text',this.value)"></div>`;
  }
  if (b.type === 'paragraph') {
    return `<div class="proc-campo-box"><textarea class="inp" style="width:100%;min-height:70px" placeholder="Texto" oninput="_procEditorCampo(${i},'text',this.value)">${_esc(d.text)}</textarea></div>`;
  }
  if (b.type === 'list') {
    return `<div class="proc-campo-box"><select class="inp" style="width:140px;margin-bottom:6px" onchange="_procEditorListaEstilo(${i}, this.value)">
      <option value="unordered" ${d.style !== 'ordered' ? 'selected' : ''}>Vi&ntilde;etas</option>
      <option value="ordered" ${d.style === 'ordered' ? 'selected' : ''}>Numerada</option>
    </select>
    <textarea class="inp" style="width:100%;min-height:70px" placeholder="Un elemento por rengl&oacute;n" oninput="_procEditorListaItems(${i}, this.value)">${_esc((d.items || []).join('\n'))}</textarea></div>`;
  }
  if (b.type === 'table') {
    const filas = d.content || [];
    const cols = (filas[0] || []).length;
    const fondos = d.filas_fondo || [];
    const borde = _procTablaBordeSano(d.borde); // con default aunque d.borde no exista todavía -- recién se escribe al tocar el control
    // Controles de estilo de TODA la tabla (ver el comentario grande de
    // _procBloqueTable para el porqué del grano tabla/fila en vez de celda).
    const controlesBorde = `<div style="display:flex;gap:8px;align-items:center;margin-bottom:8px;flex-wrap:wrap">
      <label style="font-size:11px;color:var(--muted)">Borde de celda</label>
      <select class="inp" style="width:90px" onchange="_procEditorTablaBorde(${i},'grosor',this.value)">
        ${[0, 1, 2, 3, 4, 5].map(g => `<option value="${g}" ${borde.grosor === g ? 'selected' : ''}>${g === 0 ? 'Sin borde' : g + 'px'}</option>`).join('')}
      </select>
      <input type="color" class="proc-tabla-borde-color" title="Color del borde" value="${borde.color}" oninput="_procEditorTablaBorde(${i},'color',this.value)">
    </div>`;
    const encabezado = `<div class="proc-tabla-fila">
      <span class="proc-tabla-esquina"></span>
      <span class="proc-tabla-fondo"></span>
      ${Array.from({ length: cols }).map((_, c) => `<span class="proc-tabla-col-quitar"><span class="sn-quitar" title="Quitar columna" onclick="_procEditorTablaQuitarColumna(${i},${c})">&times;</span></span>`).join('')}
    </div>`;
    const cuerpo = filas.map((fila, f) => {
      const colorFila = _procColorValido(fondos[f]) || '#ffffff'; // el swatch necesita SIEMPRE un valor; '#ffffff' es solo el arranque del picker, no significa "fondo blanco puesto"
      return `<div class="proc-tabla-fila">
      <span class="sn-quitar proc-tabla-fila-quitar" title="Quitar fila" onclick="_procEditorTablaQuitarFila(${i},${f})">&times;</span>
      <div class="proc-tabla-fondo">
        <input type="color" title="Color de fondo de la fila" value="${colorFila}" oninput="_procEditorTablaFilaFondo(${i},${f},this.value)">
        <span class="sn-quitar" title="Quitar color de fondo" onclick="_procEditorTablaFilaFondo(${i},${f},'')">&times;</span>
      </div>
      ${fila.map((c, col) => `<input class="inp proc-tabla-celda" type="text" value="${_esc(c)}" oninput="_procEditorTablaCelda(${i},${f},${col},this.value)">`).join('')}
    </div>`;
    }).join('');
    // controlesBorde va AFUERA de .proc-tabla-edit a propósito: ese div
    // scrollea horizontal si la tabla tiene muchas columnas (overflow-x:
    // auto), y el selector de borde/color no es parte de la grilla -- si
    // quedara adentro, se iría con el scroll.
    return `<div class="proc-campo-box">${controlesBorde}<div class="proc-tabla-edit">${encabezado}${cuerpo}</div>
      <div style="margin-top:6px;display:flex;gap:8px;flex-wrap:wrap">
        <button type="button" class="page-btn" onclick="_procEditorTablaAgregarFila(${i})">&#10133; Fila</button>
        <button type="button" class="page-btn" onclick="_procEditorTablaAgregarColumna(${i})">&#10133; Columna</button>
      </div></div>`;
  }
  if (b.type === 'ficha') {
    const filas = (d.filas || []).map((fila, j) => `<div class="pv-row" style="margin-bottom:4px">
      <input class="inp" type="text" placeholder="Campo" style="width:160px" value="${_esc(fila[0])}" oninput="_procEditorFichaFila(${i},${j},0,this.value)">
      <input class="inp" type="text" placeholder="Valor" style="width:260px" value="${_esc(fila[1])}" oninput="_procEditorFichaFila(${i},${j},1,this.value)">
      <span class="sn-quitar" title="Quitar fila" onclick="_procEditorFichaQuitarFila(${i},${j})">&times;</span>
    </div>`).join('');
    return `<input class="inp" type="text" placeholder="T&iacute;tulo (opcional)" style="width:100%;margin-bottom:6px" value="${_esc(d.titulo || '')}" oninput="_procEditorCampo(${i},'titulo',this.value)">
      ${filas}
      <button type="button" class="page-btn" style="margin-top:4px" onclick="_procEditorFichaAgregarFila(${i})">&#10133; Agregar fila</button>`;
  }
  if (b.type === 'pasos') {
    const totalPasos = (d.pasos || []).length;
    const pasos = (d.pasos || []).map((p, j) => _procPasoEditorHtml(i, [j], p, String(j + 1), 1, totalPasos, j)).join('');
    return `<input class="inp" type="text" placeholder="T&iacute;tulo del bloque" style="width:100%;margin-bottom:6px" value="${_esc(d.titulo || '')}" oninput="_procEditorCampo(${i},'titulo',this.value)">
      ${pasos}
      <button type="button" class="page-btn" onclick="_procEditorPasoAgregar(${i})">&#10133; Agregar paso</button>`;
  }
  if (b.type === 'anexo') {
    // Tres tipos (ver _PROC_ANEXO_TIPOS): 'generico' (ficha + archivos, lo
    // de siempre), 'archivo' (UN solo archivo -- el anexo ES ese archivo) y
    // 'procedimiento' (apunta a otro procedimiento del sistema; tocarlo
    // navega en vez de abrir la ventana chica, ver _procAbrirAnexo). 'nombre'
    // (la palabra clave) es común a los tres -- se valida al guardar en
    // _procGuardarContenido, no bloque por bloque al tipear.
    const tipo = d.tipo || 'generico';
    const selectorTipo = `<select class="inp" style="width:220px;margin-bottom:6px" onchange="_procEditorAnexoTipo(${i}, this.value)">
      ${_PROC_ANEXO_TIPOS.map(([v, l]) => `<option value="${v}" ${tipo === v ? 'selected' : ''}>${l}</option>`).join('')}
    </select>`;
    const nombreInput = `<input class="inp" type="text" placeholder="Nombre (es la palabra clave que lo abre)" style="width:100%;margin-bottom:6px" value="${_esc(d.nombre || '')}" oninput="_procEditorCampo(${i},'nombre',this.value)">`;
    if (tipo === 'archivo') {
      const a = d.archivo;
      const campo = !a || !a.fileId
        ? `<button type="button" class="page-btn" onclick="_procEditorAnexoArchivoUnicoElegir(${i})">&#128247; Subir imagen o archivo</button>`
        : `<div class="proc-vacio" style="margin-bottom:6px">${_PROC_EXT_IMAGEN.has((a.ext || '').toLowerCase()) ? '&#128247;' : '&#128196;'} ${_esc(a.nombre || 'archivo')}</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <button type="button" class="page-btn" onclick="_procEditorAnexoArchivoUnicoElegir(${i})">Reemplazar</button>
            <button type="button" class="page-btn" onclick="_procEditorAnexoArchivoUnicoQuitar(${i})">Quitar</button>
          </div>`;
      return `${selectorTipo}${nombreInput}${campo}`;
    }
    if (tipo === 'procedimiento') {
      let selectProc;
      if (_PROC_LISTA_PARA_ANEXO === null) {
        selectProc = '<div class="proc-vacio">Cargando procedimientos...</div>';
      } else {
        const opciones = _PROC_LISTA_PARA_ANEXO
          .filter(p => !_procActual || p.codigo !== _procActual.codigo)
          .map(p => `<option value="${_esc(p.codigo)}" ${d.procedimientoCodigo === p.codigo ? 'selected' : ''}>${_esc(p.titulo)}</option>`).join('');
        selectProc = `<select class="inp" style="width:100%" onchange="_procEditorAnexoProcedimiento(${i}, this.value)">
          <option value="">Eleg&iacute; un procedimiento...</option>
          ${opciones}
        </select>`;
      }
      return `${selectorTipo}${nombreInput}${selectProc}`;
    }
    // 'generico': mismo par [clave,valor] que 'ficha' -- reusa sus funciones
    // de edición de filas sin duplicar nada. 'Valor' es la única diferencia
    // con 'ficha': admite salto de línea (pedido explícito), por eso es un
    // <textarea> y no un <input> de un renglón -- la lectura (_procBloqueAnexo/
    // _procAbrirAnexo) ya lo respeta via white-space:pre-wrap en .proc-dl dd.
    const filas = (d.filas || []).map((fila, j) => `<div class="pv-row" style="margin-bottom:4px">
      <input class="inp" type="text" placeholder="Campo" style="width:160px" value="${_esc(fila[0])}" oninput="_procEditorFichaFila(${i},${j},0,this.value)">
      <textarea class="inp" placeholder="Valor" style="width:260px;min-height:32px" oninput="_procEditorFichaFila(${i},${j},1,this.value)">${_esc(fila[1])}</textarea>
      <span class="sn-quitar" title="Quitar fila" onclick="_procEditorFichaQuitarFila(${i},${j})">&times;</span>
    </div>`).join('');
    const totalArchivosAnexo = (d.archivos || []).length;
    const archivos = (d.archivos || []).map((a, k) => `<div class="pv-row" style="margin-bottom:4px;align-items:center">
      <span style="flex:1;font-size:12px">${_PROC_EXT_IMAGEN.has((a.ext || '').toLowerCase()) ? '&#128247;' : '&#128196;'} ${_esc(a.nombre || 'archivo')}</span>
      <button type="button" class="page-btn" style="padding:2px 6px" ${k === 0 ? 'disabled' : ''} onclick="_procEditorAnexoArchivoMover(${i},${k},-1)" title="Subir">&#9650;</button>
      <button type="button" class="page-btn" style="padding:2px 6px" ${k === totalArchivosAnexo - 1 ? 'disabled' : ''} onclick="_procEditorAnexoArchivoMover(${i},${k},1)" title="Bajar">&#9660;</button>
      <span class="sn-quitar" title="Quitar archivo" onclick="_procEditorAnexoArchivoQuitar(${i},${k})">&times;</span>
    </div>`).join('');
    return `${selectorTipo}${nombreInput}
      ${filas}
      <button type="button" class="page-btn" style="margin-top:4px" onclick="_procEditorFichaAgregarFila(${i})">&#10133; Agregar fila</button>
      <div style="margin-top:8px">${archivos}</div>
      <button type="button" class="page-btn" style="margin-top:4px" onclick="_procEditorAnexoArchivoElegir(${i})">&#128247; Agregar imagen/archivo</button>`;
  }
  if (b.type === 'imagen') {
    if (!d.fileId) {
      return `<button type="button" class="page-btn" onclick="_procEditorImagenElegir(${i})">&#128247; Subir imagen o PDF</button>`;
    }
    const esImg = _PROC_EXT_IMAGEN.has((d.ext || '').toLowerCase());
    return `<div class="proc-vacio" style="margin-bottom:6px">${esImg ? '&#128247;' : '&#128196;'} ${_esc(d.nombre || 'archivo')}</div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button type="button" class="page-btn" onclick="_procEditorImagenElegir(${i})">Reemplazar</button>
        <button type="button" class="page-btn" onclick="_procEditorImagenQuitar(${i})">Quitar</button>
      </div>`;
  }
  if (b.type === 'grafico') {
    // Mismo par [clave,valor] que ficha/anexo (clave=etiqueta, valor=numero)
    // -- reusa sus funciones de fila. La conversion a {labels,data} de
    // Chart.js es solo al DIBUJAR (_procDibujarGraficos), no acá.
    const filas = (d.filas || []).map((fila, j) => `<div class="pv-row" style="margin-bottom:4px">
      <input class="inp" type="text" placeholder="Etiqueta" style="width:160px" value="${_esc(fila[0])}" oninput="_procEditorFichaFila(${i},${j},0,this.value)">
      <input class="inp" type="text" placeholder="Valor" style="width:100px" value="${_esc(fila[1])}" oninput="_procEditorFichaFila(${i},${j},1,this.value)">
      <span class="sn-quitar" title="Quitar fila" onclick="_procEditorFichaQuitarFila(${i},${j})">&times;</span>
    </div>`).join('');
    return `<select class="inp" style="width:140px;margin-bottom:6px" onchange="_procEditorCampo(${i},'tipo',this.value)">
      ${_PROC_GRAFICO_TIPOS.map(([v, l]) => `<option value="${v}" ${(d.tipo || 'bar') === v ? 'selected' : ''}>${l}</option>`).join('')}
    </select>
    <input class="inp" type="text" placeholder="T&iacute;tulo (opcional)" style="width:100%;margin-bottom:6px" value="${_esc(d.titulo || '')}" oninput="_procEditorCampo(${i},'titulo',this.value)">
      ${filas}
      <button type="button" class="page-btn" style="margin-top:4px" onclick="_procEditorFichaAgregarFila(${i})">&#10133; Agregar fila</button>`;
  }
  if (b.type === 'diagrama') {
    // Sin vista previa en vivo acá adentro (mismo criterio que 'grafico',
    // que tampoco dibuja el chart mientras se edita) -- "Editar diagrama"
    // abre el editor completo (draw.io embebido, ver _procDiagramaEditorAbrir
    // más abajo), que es donde de verdad se ve y se dibuja.
    const estado = d.xml ? '<div class="proc-vacio">Diagrama cargado -- toc&aacute; "Editar diagrama" para verlo o cambiarlo.</div>' : '<div class="proc-vacio">Todav&iacute;a no tiene contenido.</div>';
    return `<input class="inp" type="text" placeholder="T&iacute;tulo (opcional)" style="width:100%;margin-bottom:6px" value="${_esc(d.titulo || '')}" oninput="_procEditorCampo(${i},'titulo',this.value)">
      ${estado}
      <button type="button" class="page-btn" style="margin-top:4px" onclick="_procDiagramaEditorAbrir(${i})">&#128256; ${d.xml ? 'Editar diagrama' : 'Crear diagrama'}</button>`;
  }
  return `<div class="proc-vacio">Este tipo de bloque ("${_esc(b.type || '?')}") todav&iacute;a no tiene editor.</div>`;
}
function _procEditorHtml() {
  const e = _procEditor;
  // Pedido explícito: cada bloque del editor a la MISMA altura que su
  // versión final, para poder compararlos mejor -- en vez de dos columnas
  // independientes (que se desalinean apenas un bloque mide distinto
  // editado que publicado), es una sola grilla de 2 columnas con UNA fila
  // de grilla por bloque (editor | vista previa); CSS Grid mide cada fila
  // por la celda más alta de las dos, así que las dos quedan a la par
  // SOLAS, sin medir nada a mano. _procActualizarPreview (el debounce de
  // cada tecla) repinta solo la celda de vista previa de cada fila por su
  // id (proc-preview-fila-N) -- nunca esta función entera, que perdería el
  // foco de lo que se esté tipeando.
  //
  // _procPreviewVisible (botón "Ocultar/Mostrar vista previa", pedido
  // explícito): poder sacarla de encima cuando solo hace falta lugar para
  // escribir, y volver a ponerla cuando se la quiere comparar -- apagada,
  // ni se calcula (nada de anexos/imágenes/gráficos/diagramas de la vista
  // previa), la grilla pasa a una sola columna.
  //
  // _procPreviewAlineada (botón "Alinear vista previa", SEPARADO del de
  // arriba -- pedido explícito porque "ocultar" y "alinear" son dos
  // decisiones distintas): con la vista previa visible, decide si los
  // bloques van en la grilla de siempre (alineados, una fila de grilla por
  // bloque) o en dos columnas INDEPENDIENTES que fluyen cada una a su
  // altura real ("modo real"), mismos ids (proc-preview-fila-N) en los dos
  // casos -- _procActualizarPreview no necesita saber en cuál de los dos
  // está.
  const bloquesLimpios = e.blocks.map(_procEditorLimpiarBloque);
  let filas;
  if (_procPreviewVisible) {
    const anexosPreview = _procAnexosDe(bloquesLimpios);
    const filaEditor = (b, i) => `<div class="proc-fila-editor">${_procEditorBloqueHtml(b, i)}</div>`;
    const filaPreview = (i) => `<div class="proc-fila-preview" id="proc-preview-fila-${i}">${_procVistaPreviaBloqueHtml(bloquesLimpios[i], anexosPreview, i)}</div>`;
    if (_procPreviewAlineada) {
      // 'pasos' es un caso especial dentro de "alineada" (pedido
      // explícito: que el botón "Agregar subpaso" del paso 1 quede a la
      // altura de "1.1" en la vista previa, no a la del bloque entero) --
      // cada paso/subpaso/subsubpaso es su PROPIA fila, en vez de la fila
      // única por bloque que usan los demás tipos (ver
      // _procEditorBloquePasosAlineadoFilas). Siempre con el bloque CRUDO
      // (b, no bloquesLimpios[i]): la cantidad y el orden de pasos tienen
      // que ser IDÉNTICOS entre editor y vista previa fila por fila, y
      // limpiar filtra los pasos totalmente vacíos -- correcto para el
      // documento final, pero rompería esa correspondencia acá mismo
      // mientras se está escribiendo uno nuevo.
      filas = e.blocks.map((b, i) => b.type === 'pasos'
        ? _procEditorBloquePasosAlineadoFilas(b, i, anexosPreview)
        : filaEditor(b, i) + filaPreview(i)
      ).join('');
    } else {
      filas = '<div class="proc-editor-col">' + e.blocks.map((b, i) => filaEditor(b, i)).join('') + '</div>'
        + '<div class="proc-preview-col">' + e.blocks.map((b, i) => filaPreview(i)).join('') + '</div>';
    }
    // Primer pintado del panel entero (abrir el editor, o un cambio
    // estructural vía _procRefrescarse): hace falta volver a cargar
    // imágenes/dibujar gráficos/diagramas de la vista previa igual que
    // haría _procRenderLectura -- _procActualizarPreview (el debounce de
    // cada tecla) no alcanza a correr todavía porque recién ahora existen
    // los <canvas>/<div class="mxgraph">/<img> reales en el DOM.
    setTimeout(() => {
      _procCargarImagenesLocal(bloquesLimpios);
      _procDibujarGraficos({ blocks: bloquesLimpios });
      if (bloquesLimpios.some(b => b && b.type === 'diagrama')) _procProcesarDiagramas();
    }, 0);
  } else {
    filas = e.blocks.map((b, i) => `<div class="proc-fila-editor">${_procEditorBloqueHtml(b, i)}</div>`).join('');
  }
  return `<input type="file" id="proc-img-input" accept="image/*,application/pdf" style="display:none" onchange="_procEditorImagenArchivoElegido(this)">
    <div class="proc-anexo-form">
    <div class="card-title" style="margin-bottom:8px;display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap">
      <span>Editar procedimiento</span>
      <span style="display:flex;gap:6px">
        <button type="button" class="page-btn" style="font-size:11px;padding:3px 9px;text-transform:none;font-weight:normal" onclick="_procToggleSidebar()">${document.body.classList.contains('proc-full') ? '&#9776; Mostrar men&uacute;' : '&#9776; Ocultar men&uacute; (m&aacute;s lugar)'}</button>
      </span>
    </div>
    ${_procToolbarHtml()}
    <input class="inp" type="text" placeholder="T&iacute;tulo" style="width:100%;margin-bottom:10px" value="${_esc(e.titulo)}" oninput="_procEditorTitulo(this.value)">
    ${_procPreviewVisible ? '<div class="proc-editor-grid-etiquetas"><span>Editor</span><span>&#128065; As&iacute; va quedando</span></div>' : ''}
    <div class="proc-editor-grid${!_procPreviewVisible ? ' proc-sin-preview' : (_procPreviewAlineada ? '' : ' proc-modo-real')}">${filas || '<div class="proc-vacio">Sin bloques todav&iacute;a -- agreg&aacute; uno de la barra de arriba.</div>'}</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:10px">
      <button type="button" class="page-btn" onclick="_procEditorGuardar()">Guardar</button>
      <button type="button" class="page-btn" onclick="_procCerrarEditor()">Cerrar</button>
      <button type="button" class="page-btn" style="color:var(--red)" onclick="_procDescartarBorrador()">Descartar borrador</button>
      <span id="proc-sucio" style="font-size:12px;margin-left:auto"></span>
    </div>
  </div>`;
}
// Antes de guardar: saca renglones/filas vacíos que solo están ahí porque el
// textarea/fila arranca en blanco. Los tipos que el editor no toca
// (header/paragraph/imagen/desconocidos) se mandan tal cual. 'anexo' solo
// recorta espacios de 'nombre' acá -- que no esté vacío y que no se repita
// es una validación de TODO el documento (dos bloques entre sí), así que
// vive en _procEditorGuardar, no en esta limpieza por-bloque.
// Un renglón vacío DENTRO del cuerpo (entre dos renglones con texto) es un
// separador a propósito, no ruido -- se conserva tal cual para pintarse
// como renglón en blanco (ver _procPasoHtml/_procPasoHtmlPropio), nunca
// como un <li> con el punto suelto. Los vacíos de los BORDES (al
// principio o al final) sí son ruido -- el textarea de un paso nuevo
// arranca con un renglón sin tipear, y eso tiene que poder seguir
// descartándose solo (ver _procLimpiarPasos, "tiene cuerpo").
function _procLimpiarDetalles(detalles) {
  const arr = (detalles || []).map(s => s.trim());
  while (arr.length && !arr[0]) arr.shift();
  while (arr.length && !arr[arr.length - 1]) arr.pop();
  return arr;
}
// Recorre pasos/subpasos/subsubpasos por igual: se filtra el que no tiene
// NADA en ningún nivel (ni título, ni cuerpo, ni hijos) -- el título no es
// obligatorio para que un paso tenga cuerpo (detalles/nota/archivos), así
// que la condición de descarte es "todo vacío a la vez", no "sin título".
function _procLimpiarPasos(pasos) {
  return (pasos || []).map(p => {
    const detalles = _procLimpiarDetalles(p.detalles);
    const nota = (p.nota || '').trim() || undefined;
    const archivos = p.archivos || [];
    const subpasos = _procLimpiarPasos(p.subpasos);
    return { titulo: p.titulo, detalles, nota, archivos, subpasos };
  }).filter(p => (p.titulo || '').trim() || p.detalles.length || p.nota || p.archivos.length || p.subpasos.length);
}
function _procEditorLimpiarBloque(b) {
  const d = b.data || {};
  if (b.type === 'list') return { type: b.type, data: { style: d.style, items: (d.items || []).map(s => s.trim()).filter(Boolean) } };
  if (b.type === 'table') {
    // filas_fondo tiene que perder EXACTAMENTE las mismas filas que se
    // recortan de content (si no, un color quedaría apuntando a la fila de
    // al lado tras guardar) -- por eso se filtra con el mismo criterio,
    // índice a índice, en vez de volver a correr el .filter por separado.
    const fondosOriginal = d.filas_fondo || [];
    const indicesConservados = [];
    const content = (d.content || []).filter((f, idx) => {
      const queda = f.some(c => (c || '').trim());
      if (queda) indicesConservados.push(idx);
      return queda;
    });
    const data = { content };
    if (d.borde) data.borde = _procTablaBordeSano(d.borde);
    const filasFondo = indicesConservados.map(idx => _procColorValido(fondosOriginal[idx]) || '');
    if (filasFondo.some(Boolean)) data.filas_fondo = filasFondo; // no guarda el array si ninguna fila tiene color -- tabla sin personalizar queda con el payload de siempre
    return { type: b.type, data };
  }
  if (b.type === 'ficha') return { type: b.type, data: { titulo: d.titulo || undefined, filas: (d.filas || []).filter(([k, v]) => (k || '').trim() || (v || '').trim()) } };
  if (b.type === 'anexo') return { type: b.type, data: {
    tipo: d.tipo || 'generico',
    nombre: (d.nombre || '').trim(),
    filas: (d.filas || []).filter(([k, v]) => (k || '').trim() || (v || '').trim()),
    archivos: d.archivos || [],
    archivo: d.archivo || null,
    procedimientoCodigo: d.procedimientoCodigo || null,
    procedimientoTitulo: d.procedimientoTitulo || '',
  } };
  if (b.type === 'grafico') return { type: b.type, data: { tipo: d.tipo || 'bar', titulo: d.titulo || undefined, filas: (d.filas || []).filter(([k, v]) => (k || '').trim() || (v || '').trim()) } };
  if (b.type === 'diagrama') return { type: b.type, data: { titulo: (d.titulo || '').trim() || undefined, xml: d.xml || '' } };
  if (b.type === 'pasos') {
    return { type: b.type, data: {
      titulo: d.titulo || 'Pasos',
      pasos: _procLimpiarPasos(d.pasos),
    } };
  }
  return b;
}
// ── Editor de diagramas (draw.io embebido) ──────────────────────────────────
// Único bloque cuya edición NO es un formulario inline: abre un editor
// completo aparte (draw.io vendorizado en frontend/js/vendor/drawio/),
// corriendo en un <iframe> en modo embebido (?embed=1&proto=json), que
// habla con esta página por postMessage -- protocolo confirmado contra
// https://www.drawio.com/docs/reference/embed-mode/ (eventos que manda el
// iframe: 'init' al estar listo para recibir el diagrama, 'save' con el
// xml nuevo cada vez que se guarda ahí adentro, 'exit' al cerrar; la única
// acción que le mandamos es 'load', con el xml para arrancar).
let _procDiagramaModalActual = null;
let _procDiagramaBloqueActual = null; // índice del bloque que se está editando, mientras el modal está abierto
let _procDiagramaMensajeHandler = null;
// Diagrama vacío válido (una página A4 en blanco) -- lo que ve quien toca
// "Crear diagrama" en un bloque que todavía no tiene xml.
const _PROC_DIAGRAMA_XML_VACIO = '<mxGraphModel dx="800" dy="600" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="850" pageHeight="1100" math="0" shadow="0"><root><mxCell id="0"/><mxCell id="1" parent="0"/></root></mxGraphModel>';
function _procDiagramaEditorCerrar() {
  if (_procDiagramaMensajeHandler) { window.removeEventListener('message', _procDiagramaMensajeHandler); _procDiagramaMensajeHandler = null; }
  if (_procDiagramaModalActual) { _procDiagramaModalActual.remove(); _procDiagramaModalActual = null; }
  _procDiagramaBloqueActual = null;
}
function _procDiagramaEditorAbrir(i) {
  if (!_procEditor || !_procEditor.blocks[i]) return;
  _procDiagramaEditorCerrar(); // por si quedó uno abierto de antes (no debería, pero es gratis)
  _procDiagramaBloqueActual = i;
  const ov = document.createElement('div');
  ov.className = 'proc-anexo-modal';
  ov.innerHTML = `<div class="proc-anexo-modal-box" style="width:min(96vw,1100px);height:min(90vh,780px);display:flex;flex-direction:column;padding:10px">
    <div class="card-title" style="margin-bottom:6px">Editor de diagramas</div>
    <iframe id="proc-diagrama-iframe" src="/js/vendor/drawio/index.html?embed=1&amp;proto=json&amp;spin=1&amp;ui=min" style="flex:1;border:1px solid var(--border);border-radius:6px;background:#fff"></iframe>
    <div style="margin-top:8px;display:flex;justify-content:space-between;align-items:center">
      <span id="proc-diagrama-estado" class="proc-vacio">Cargando el editor...</span>
      <button type="button" class="page-btn" onclick="_procDiagramaEditorCerrar()">Cerrar</button>
    </div>
  </div>`;
  document.body.appendChild(ov);
  _procDiagramaModalActual = ov;
  // Si el iframe nunca contesta 'init' (archivo faltante, red caída) no se
  // queda esperando para siempre -- mismo criterio defensivo que el resto
  // de este archivo ante lo que puede fallar.
  const avisoSiNoCarga = setTimeout(() => {
    const e = $id('proc-diagrama-estado');
    if (e) e.textContent = 'No se pudo abrir el editor de diagramas.';
  }, 8000);
  _procDiagramaMensajeHandler = (ev) => {
    const iframe = $id('proc-diagrama-iframe');
    // ev.source (no ev.origin) identifica a ESTE iframe puntual -- por si
    // hubiera otro listener de 'message' en la página (no hay hoy, pero no
    // cuesta nada ser precisos en vez de confiar en que nadie más escuche).
    if (!iframe || ev.source !== iframe.contentWindow) return;
    let msg;
    try { msg = JSON.parse(ev.data); } catch (e) { return; }
    const estado = $id('proc-diagrama-estado');
    if (msg.event === 'init') {
      clearTimeout(avisoSiNoCarga);
      const bloque = _procEditor && _procEditor.blocks[_procDiagramaBloqueActual];
      const xml = (bloque && bloque.data && bloque.data.xml) || _PROC_DIAGRAMA_XML_VACIO;
      iframe.contentWindow.postMessage(JSON.stringify({ action: 'load', xml }), window.location.origin);
      if (estado) estado.textContent = '';
    } else if (msg.event === 'save') {
      const bloque = _procEditor && _procEditor.blocks[_procDiagramaBloqueActual];
      if (bloque) { bloque.data.xml = msg.xml; _procSucio(true); }
      if (estado) estado.textContent = 'Guardado.';
    } else if (msg.event === 'exit') {
      _procDiagramaEditorCerrar();
      _procRefrescarse(); // repinta el boton ("Crear" -> "Editar diagrama") y el estado ("sin contenido" -> "cargado")
    }
  };
  window.addEventListener('message', _procDiagramaMensajeHandler);
}
// ── Guardado automático del borrador ────────────────────────────────────────
// Mismo mecanismo que _snAutoguardar en sinterizado.js (un solo temporizador
// compartido, reintento silencioso ante CONFLICTO) aplicado al endpoint del
// borrador. Nunca bloquea el autoguardado por el título vacío -- esa
// validación es solo al ENVIAR (ver _procEditorGuardar); autoguardar con un
// título vacío a mitad de tipear no tiene que mostrar un error.
const _PROC_AUTOGUARDADO_ESPERA = 1200;
let _procAutoguardadoTimer = null;
let _procAutoguardadoEnCurso = false;
let _procAutoguardadoPendiente = false;
function _procProgramarGuardado(inmediato) {
  if (!_procEditor) return;
  clearTimeout(_procAutoguardadoTimer);
  if (inmediato) _procAutoguardar();
  else _procAutoguardadoTimer = setTimeout(_procAutoguardar, _PROC_AUTOGUARDADO_ESPERA);
}
function _procSucio(inmediato) {
  _procEstadoGuardado('&#9679; cambios sin guardar', 'var(--yellow)');
  _procProgramarGuardado(inmediato);
  _procActualizarPreview();
}
function _procEstadoGuardado(html, color) {
  const s = $id('proc-sucio');
  if (s) { s.style.color = color || ''; s.innerHTML = html; }
}
async function _procAutoguardar() {
  if (_procAutoguardadoEnCurso) { _procAutoguardadoPendiente = true; return; }
  if (!_procEditor || !_procActual) return;
  clearTimeout(_procAutoguardadoTimer);
  _procAutoguardadoEnCurso = true;
  _procAutoguardadoPendiente = false;
  _procEstadoGuardado('&#9679; guardando...', 'var(--muted)');
  try {
    for (let intentos = 1; ; intentos++) {
      const titulo = (_procEditor.titulo || '').trim();
      const body = { borrador_modificado_en: _procBorradorModificadoEn, contenido: { blocks: _procEditor.blocks.map(_procEditorLimpiarBloque) } };
      if (titulo) body.titulo = titulo; // vacío mientras tipea: no lo manda, no lo rechaza el servidor
      try {
        const d = await api('/api/procedimientos/' + _procActual.id + '/borrador', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        _procBorradorModificadoEn = d.borrador_modificado_en;
        _procActual = d;
        if (_procOnActualizar) _procOnActualizar(d);
        if (_procAutoguardadoPendiente) _procEstadoGuardado('&#9679; cambios sin guardar', 'var(--yellow)');
        else _procEstadoGuardado('Guardado autom&aacute;ticamente', 'var(--green)');
        return;
      } catch (e) {
        const m = _procMsgError(e);
        if (m.startsWith('CONFLICTO') && intentos < 3) {
          try { _procBorradorModificadoEn = (await api('/api/procedimientos/' + _procActual.id)).borrador_modificado_en; }
          catch (e2) { /* sigue con la marca vieja; se vuelve a pedir en el próximo intento */ }
          continue;
        }
        _procEstadoGuardado('&#9888; sin guardar: ' + _esc(m), 'var(--red)');
        _procAutoguardadoTimer = setTimeout(_procAutoguardar, 8000);
        return;
      }
    }
  } finally {
    _procAutoguardadoEnCurso = false;
    if (_procAutoguardadoPendiente) { _procAutoguardadoPendiente = false; _procAutoguardar(); }
  }
}

// ── Enviar a aprobación ("Guardar" en el editor) ────────────────────────────
// Valida todo lo de siempre (título, nombres de anexo) y, si está todo
// bien, pide el motivo del cambio en una ventana chica (ver
// _procAbrirEnviarModal) -- nunca window.prompt(), no es el estilo de esta
// app. _procConfirmarEnvio hace el autoguardado final (por si el debounce
// todavía no corrió) y recién ahí el POST .../enviar.
function _procEditorGuardar() {
  if (!_procPuedeEditar() || !_procEditor || !_procActual || _procActual.error) return;
  const titulo = (_procEditor.titulo || '').trim();
  if (!titulo) { alert('El título es obligatorio.'); return; }
  if (titulo.length > 300) { alert('El título es demasiado largo (máximo 300 caracteres).'); return; }
  const nombresAnexo = [];
  for (const b of _procEditor.blocks) {
    if (b.type !== 'anexo') continue;
    const nombre = (b.data.nombre || '').trim();
    if (!nombre) { alert('Cada anexo necesita un nombre (es la palabra clave que lo abre).'); return; }
    if (nombre.length > 100) { alert('El nombre de un anexo es demasiado largo (máximo 100 caracteres).'); return; }
    const clave = nombre.toLowerCase();
    if (nombresAnexo.includes(clave)) { alert('Hay dos anexos con el mismo nombre ("' + nombre + '").'); return; }
    nombresAnexo.push(clave);
  }
  _procAbrirEnviarModal();
}
let _procEnviarModalActual = null;
function _procCerrarEnviarModal() {
  if (_procEnviarModalActual) { _procEnviarModalActual.remove(); _procEnviarModalActual = null; }
}
function _procAbrirEnviarModal() {
  _procCerrarEnviarModal();
  const ov = document.createElement('div');
  ov.className = 'proc-anexo-modal';
  ov.innerHTML = `<div class="proc-anexo-modal-box">
    <div class="card-title">Enviar a aprobaci&oacute;n</div>
    <div class="proc-vacio" style="margin-bottom:8px">Explic&aacute; qu&eacute; cambiaste -- lo va a leer quien lo apruebe.</div>
    <textarea class="inp" id="proc-motivo-envio" style="width:100%;min-height:70px" placeholder="Ej: se corrigi&oacute; el paso 3 y se agreg&oacute; una foto"></textarea>
    <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">
      <button type="button" class="page-btn" id="proc-enviar-confirmar" onclick="_procConfirmarEnvio()">Enviar</button>
      <button type="button" class="page-btn" onclick="_procCerrarEnviarModal()">Cancelar</button>
    </div>
  </div>`;
  ov.onclick = (e) => { if (e.target === ov) _procCerrarEnviarModal(); };
  document.body.appendChild(ov);
  _procEnviarModalActual = ov;
}
async function _procConfirmarEnvio() {
  if (_procTourActivo) return; // ver _procAbrirEditor
  if (!_procEditor || !_procActual) return;
  const inp = $id('proc-motivo-envio');
  const motivo = ((inp && inp.value) || '').trim();
  if (!motivo) { alert('Hace falta explicar qué se cambió.'); return; }
  clearTimeout(_procAutoguardadoTimer);
  const btn = $id('proc-enviar-confirmar');
  if (btn) { btn.disabled = true; btn.textContent = 'Enviando...'; }
  try {
    const titulo = (_procEditor.titulo || '').trim();
    await api('/api/procedimientos/' + _procActual.id + '/borrador', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ borrador_modificado_en: _procBorradorModificadoEn, titulo, contenido: { blocks: _procEditor.blocks.map(_procEditorLimpiarBloque) } }),
    });
    const enviado = await api('/api/procedimientos/' + _procActual.id + '/borrador/enviar', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ motivo }),
    });
    _procActual = enviado;
    _procEditor = null;
    _procBorradorModificadoEn = null;
    if (_procOnActualizar) _procOnActualizar(enviado);
    _procCerrarEnviarModal();
    _procRefrescarse();
  } catch (e) {
    alert('No se pudo enviar: ' + _procMsgError(e));
    if (btn) { btn.disabled = false; btn.textContent = 'Enviar'; }
  }
}

// ── Aprobar / rechazar / descartar ──────────────────────────────────────────
// Control de 4 ojos: el backend ya rechaza con 403 si quien aprueba/rechaza
// es el propio autor del borrador (ver post_procedimiento_borrador_aprobar
// en main.py) -- acá ni se ofrece el botón (_procBannerBorradorHtml), esto
// es la segunda barrera, no la única.
async function _procAprobarBorrador() {
  if (_procTourActivo) return; // ver _procAbrirEditor
  if (!_procPuedeEditar() || !_procActual) return;
  if (!confirm('¿Aprobar este borrador? Reemplaza el contenido publicado y sube la revisión.')) return;
  try {
    const d = await api('/api/procedimientos/' + _procActual.id + '/borrador/aprobar', { method: 'POST' });
    _procActual = d;
    if (_procOnActualizar) _procOnActualizar(d);
    _procRefrescarse();
  } catch (e) {
    alert('No se pudo aprobar: ' + _procMsgError(e));
  }
}
async function _procRechazarBorrador() {
  if (_procTourActivo) return; // ver _procAbrirEditor
  if (!_procPuedeEditar() || !_procActual) return;
  if (!confirm('¿Rechazar este borrador? Vuelve a modo edición para que el autor lo corrija.')) return;
  try {
    const d = await api('/api/procedimientos/' + _procActual.id + '/borrador/rechazar', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}),
    });
    _procActual = d;
    if (_procOnActualizar) _procOnActualizar(d);
    _procRefrescarse();
  } catch (e) {
    alert('No se pudo rechazar: ' + _procMsgError(e));
  }
}
async function _procDescartarBorrador() {
  if (_procTourActivo) return; // ver _procAbrirEditor
  if (!_procPuedeEditar() || !_procActual) return;
  if (!confirm('¿Descartar este borrador? Se pierde todo lo que se escribió ahí.')) return;
  clearTimeout(_procAutoguardadoTimer);
  try {
    const d = await api('/api/procedimientos/' + _procActual.id + '/borrador', { method: 'DELETE' });
    _procActual = d;
    _procEditor = null;
    _procBorradorModificadoEn = null;
    if (_procOnActualizar) _procOnActualizar(d);
    _procRefrescarse();
  } catch (e) {
    alert('No se pudo descartar: ' + _procMsgError(e));
  }
}

// Mismo criterio que el resto de la app (ver _snMsgError en sinterizado.js),
// duplicado chico a propósito para no depender de otro módulo.
function _procMsgError(e) {
  let m = String((e && e.message) || e);
  try { const j = JSON.parse(m); if (j && j.detail) m = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail); } catch (_) { /* no era JSON */ }
  return m;
}

// ── Carga por código, con cache en memoria del módulo que llama ─────────────
// No guarda estado propio (a propósito, para que cada módulo consumidor
// decida cuándo recargar) -- devuelve {contenido,...} o {error} si falló,
// nunca lanza, así el que llama no necesita un try/catch.
async function _procCargarPorCodigo(codigo) {
  try { return await api('/api/procedimientos/por-codigo/' + encodeURIComponent(codigo)); }
  catch (e) { return { error: _procMsgError(e) }; }
}

// ── Crear procedimiento nuevo (desde la pestaña principal) ─────────────────
// Mismo permiso que el resto de "editar" (procedimientos_editar o admin).
// Formulario chico (código/título/sección que lo puede ver) que arma un
// documento vacío ({blocks:[]} -- lo pone el backend si no se manda
// 'contenido') vía POST y lleva directo a su detalle: hoy ahí es donde se le
// puede agregar contenido (anexos, por ahora -- el editor de bloques en sí
// es la Fase 3, todavía pendiente).
let _procFormNuevo = null; // { codigo, titulo, seccion_ver, revision, tipo_id } o null si está cerrado

function _procAbrirFormNuevo() {
  if (!_procPuedeEditar()) return;
  _procFormNuevo = { codigo: '', titulo: '', seccion_ver: '', revision: '0', tipo_id: '' };
  _procPintarLista();
}
function _procCerrarFormNuevo() {
  _procFormNuevo = null;
  _procPintarLista();
}
function _procFormNuevoCampo(campo, valor) {
  if (!_procFormNuevo) return;
  _procFormNuevo[campo] = valor;
}
// Solo las secciones que son una PÁGINA real -- mismo criterio visual que
// Usuarios → Permisos: 'Datos sensibles' son permisos transversales (ver
// montos en $, editar la curva, editar procedimientos...), no un lugar
// donde "ver este procedimiento" tenga sentido como requisito.
function _procSeccionesOpciones() {
  return (typeof SECCIONES !== 'undefined' ? SECCIONES : []).filter(s => s.grupo !== 'Datos sensibles');
}
function _procFormNuevoHtml() {
  if (!_procFormNuevo) {
    return `<div style="margin-bottom:12px"><button type="button" class="page-btn" onclick="_procAbrirFormNuevo()">&#10133; Nuevo procedimiento</button></div>`;
  }
  const f = _procFormNuevo;
  const opciones = _procSeccionesOpciones().map(s => `<option value="${_esc(s.id)}" ${f.seccion_ver === s.id ? 'selected' : ''}>${_esc(s.label)}</option>`).join('');
  const opcionesTipo = (_PROC_TIPOS_CATALOGO || []).map(t => `<option value="${t.id}" ${String(f.tipo_id) === String(t.id) ? 'selected' : ''}>${_esc(t.sigla)} &middot; ${_esc(t.nombre)}</option>`).join('');
  return `<div class="proc-anexo-form" style="margin-bottom:12px">
    <div class="card-title" style="margin-bottom:8px">Nuevo procedimiento</div>
    <input class="inp" type="text" placeholder="C&oacute;digo (identificador &uacute;nico, sin espacios)" style="width:100%;max-width:320px;margin-bottom:8px" value="${_esc(f.codigo)}" oninput="_procFormNuevoCampo('codigo', this.value)">
    <input class="inp" type="text" placeholder="T&iacute;tulo" style="width:100%;max-width:320px;margin-bottom:8px" value="${_esc(f.titulo)}" oninput="_procFormNuevoCampo('titulo', this.value)">
    <select class="inp" style="width:100%;max-width:320px;margin-bottom:8px" onchange="_procFormNuevoCampo('tipo_id', this.value)">
      <option value="" ${!f.tipo_id ? 'selected' : ''}>Sin tipo</option>
      ${opcionesTipo}
    </select>
    <select class="inp" style="width:100%;max-width:320px;margin-bottom:8px" onchange="_procFormNuevoCampo('seccion_ver', this.value)">
      <option value="" ${!f.seccion_ver ? 'selected' : ''}>Sin secci&oacute;n (solo quienes editan procedimientos)</option>
      ${opciones}
    </select>
    <input class="inp" type="number" min="0" max="999" placeholder="Revisi&oacute;n inicial" style="width:140px;margin-bottom:8px" value="${_esc(f.revision)}" oninput="_procFormNuevoCampo('revision', this.value)">
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <button type="button" class="page-btn" id="proc-nuevo-guardar" onclick="_procCrearProcedimiento()">Crear</button>
      <button type="button" class="page-btn" onclick="_procCerrarFormNuevo()">Cancelar</button>
    </div>
  </div>`;
}
async function _procCrearProcedimiento() {
  if (_procTourActivo) return; // ver _procAbrirEditor
  if (!_procPuedeEditar() || !_procFormNuevo) return;
  const codigo = (_procFormNuevo.codigo || '').trim();
  const titulo = (_procFormNuevo.titulo || '').trim();
  if (!codigo) { alert('Hace falta un código.'); return; }
  if (codigo.length > 100) { alert('El código es demasiado largo (máximo 100 caracteres).'); return; }
  if (!titulo) { alert('Hace falta un título.'); return; }
  if (titulo.length > 300) { alert('El título es demasiado largo (máximo 300 caracteres).'); return; }
  const revision = Number(_procFormNuevo.revision);
  if (!Number.isInteger(revision) || revision < 0 || revision > 999) { alert('La revisión inicial tiene que ser un número entero entre 0 y 999.'); return; }
  const seccion_ver = _procFormNuevo.seccion_ver || null;
  const tipo_id = _procFormNuevo.tipo_id ? Number(_procFormNuevo.tipo_id) : null;
  const btn = $id('proc-nuevo-guardar');
  if (btn) { btn.disabled = true; btn.textContent = 'Creando...'; }
  try {
    const creado = await api('/api/procedimientos', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ codigo, titulo, seccion_ver, revision, tipo_id }),
    });
    _procFormNuevo = null;
    go('procedimiento_detalle', creado.codigo);
  } catch (e) {
    alert('No se pudo crear el procedimiento: ' + _procMsgError(e));
    if (btn) { btn.disabled = false; btn.textContent = 'Crear'; }
  }
}

// ── Pestaña principal "Procedimientos" (menú lateral) ───────────────────────
// Listado de TODOS los procedimientos que la persona puede ver (de
// cualquier módulo -- el backend ya filtra por seccion_ver, acá no hay que
// repetir ese chequeo) + abrir uno para verlo/editar sus anexos, con el
// mismo _procRegistrar/_procRenderPagina que usa Sinterizado embebido en su
// propia pestaña. No depende de ningún estado de otro módulo.
async function showProcedimientos() {
  _procEstilos();
  const tipos = _procPuedeEditar() ? '<button class="page-btn" style="font-size:12px;padding:3px 11px;border-radius:20px" onclick="_procTiposAbrir()" title="Crear, modificar o eliminar tipos de documento">&#9881; Tipos de documento</button>' : '';
  setMain('<div class="sec-title" style="display:flex;align-items:center;gap:10px">&#128214; Procedimientos'
    + tipos
    + '<button class="page-btn" style="font-size:12px;padding:3px 11px;border-radius:20px" onclick="_procIniciarTour()" title="Ayuda">&#10067; Ayuda</button></div>'
    + '<div id="proc-principal"><div class="loading">Cargando...</div></div>');
  // Igual que en _procAbrirDesdeLista: el catálogo de tipos no hace falta
  // para pintar la tabla (cada fila ya trae su tipo resuelto desde el
  // backend) -- se dispara en paralelo, sin esperarlo, solo para cuando
  // haga falta un selector (formulario "Nuevo procedimiento" o el modal de
  // tipos).
  if (_PROC_TIPOS_CATALOGO === null) _procCargarTiposCatalogo();
  await _procCargarLista();
  _procPintarLista();
}
async function _procCargarLista() {
  try { _PROC_LISTA = await api('/api/procedimientos'); _PROC_LISTA_ERROR = null; }
  catch (e) { _PROC_LISTA = []; _PROC_LISTA_ERROR = _procMsgError(e); }
}
let _PROC_LISTA = null;
let _PROC_LISTA_ERROR = null;
// Catálogo de tipos de documento (PE/PG + lo que se vaya cargando a mano,
// ver _procTiposAbrir más abajo) -- se carga una vez por entrada a la
// pestaña, igual que _PROC_LISTA; lo usan el selector de "Nuevo
// procedimiento", el modal de "Editar código y tipo", y la propia pantalla
// de administración de tipos.
let _PROC_TIPOS_CATALOGO = null;
async function _procCargarTiposCatalogo() {
  try { _PROC_TIPOS_CATALOGO = await api('/api/procedimientos/tipos'); }
  catch (e) { _PROC_TIPOS_CATALOGO = []; }
}
function _procPintarLista() {
  _procRefrescar('proc-principal', _procListaHtml());
}
function _procListaHtml() {
  const nuevo = _procPuedeEditar() ? _procFormNuevoHtml() : '';
  if (_PROC_LISTA_ERROR) return `${nuevo}<div class="card"><div class="proc-vacio" style="color:var(--red)">${_esc(_PROC_LISTA_ERROR)}</div></div>`;
  const filas = (_PROC_LISTA || []).map(p => `<tr class="tr-link" onclick="go('procedimiento_detalle','${_esc(p.codigo)}')">
    <td>${p.tipo_sigla ? `<span class="proc-codigo-badge" title="${_esc(p.tipo_nombre || '')}">${_esc(p.tipo_sigla)}</span>` : '<span style="color:var(--muted)">&mdash;</span>'}</td>
    <td>${_esc(p.titulo)} ${_procCodigoBadgeHtml(p.codigo)}</td>
    <td>${p.seccion_ver ? _esc(p.seccion_ver) : '<span style="color:var(--muted)">&mdash;</span>'}</td>
    <td style="color:var(--muted);font-size:12px">${_esc(p.modificado_por_nombre || '')} &middot; ${fmtFechaCorta(p.modificado_en)}</td>
  </tr>`).join('');
  return `${nuevo}<div class="card"><div class="tbl-wrap">${(_PROC_LISTA || []).length ? `<table>
      <thead><tr><th>Tipo documento</th><th>Documento</th><th>Secci&oacute;n</th><th>&Uacute;ltima modificaci&oacute;n</th></tr></thead>
      <tbody>${filas}</tbody></table>`
    : '<div class="proc-vacio">No hay procedimientos para mostrar todav&iacute;a (o ninguno de los que existen es de una secci&oacute;n a la que tengas acceso).</div>'}</div></div>`;
}
// Se llama como go('procedimiento_detalle', codigo) (ver el dispatch de
// go() en index.html), nunca directo -- así el historial/back del navegador
// y el resaltado del ítem del menú funcionan igual que en el resto de la app.
async function _procAbrirDesdeLista(codigo) {
  setMain('<div class="loading">Cargando...</div>');
  // Dispara la carga del catálogo de tipos en PARALELO, SIN esperarla --
  // esta pantalla no lo necesita para su primer pintado (el tipo de cada
  // fila, cuando aplica, ya viene resuelto desde el backend); solo hace
  // falta para el selector del modal de "Editar código y tipo", que recién
  // se abre con otro click bastante más tarde, tiempo de sobra para que
  // esto ya haya terminado.
  if (_PROC_TIPOS_CATALOGO === null) _procCargarTiposCatalogo();
  const proc = await _procCargarPorCodigo(codigo);
  _procEstilos();
  const titulo = (proc && proc.titulo) || 'Procedimiento';
  setMain(`<div class="sec-title">${_esc(titulo)} ${_procCodigoBadgeHtml(proc && proc.codigo)}</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <button type="button" class="page-btn" onclick="go('procedimientos')">&larr; Volver a Procedimientos</button>
      ${_procPuedeEliminar(proc) ? '<button type="button" class="page-btn" onclick="_procEditorCodigoAbrir()">&#9998; Editar c&oacute;digo y tipo</button>' : ''}
      ${_procPuedeEliminar(proc) ? '<button type="button" class="page-btn" style="color:var(--red)" onclick="_procEliminar()">&#128465; Eliminar procedimiento</button>' : ''}
    </div>
    <div id="proc-pagina" style="margin-top:12px"></div>`);
  _procRegistrar(proc, 'proc-pagina', null);
  _procRefrescarse();
}
// Archiva (nunca borra de verdad -- ver DELETE /api/procedimientos/{id} en
// main.py): deja de verse en la lista y en cualquier pantalla que lo
// consuma (ej. la pestaña Procedimiento de Sinterizado quedaría mostrando
// el error de "no encontrado" si alguien borra el que tiene asignado -- por
// eso este botón solo vive acá, en la lista general, nunca colgado de
// _procRenderPagina/_procRegistrar que es lo que usan los módulos que
// EMBEBEN un procedimiento puntual).
function _procPuedeEliminar(proc) {
  return !!(proc && !proc.error && _procPuedeEditar());
}
async function _procEliminar() {
  if (_procTourActivo) return; // ver _procAbrirEditor
  if (!_procPuedeEditar() || !_procActual || _procActual.error) return;
  if (!confirm('¿Eliminar el procedimiento "' + (_procActual.titulo || '') + '"?\n\nDeja de verse en la lista y en cualquier pantalla que lo use. No se puede deshacer desde acá.')) return;
  try {
    await api('/api/procedimientos/' + _procActual.id, { method: 'DELETE' });
    go('procedimientos');
  } catch (e) {
    alert('No se pudo eliminar: ' + _procMsgError(e));
  }
}

// Editar el código y el tipo de documento (pedido explícito): 'codigo' es
// la clave estable que guardan los anexos tipo 'procedimiento' de OTROS
// documentos -- al cambiarlo, put_procedimiento (main.py) actualiza esas
// referencias solo, así que acá alcanza con mandar el código nuevo y
// renavegar a la URL que le corresponde (mismo patrón que al crear uno
// nuevo, ver _procFormNuevoCrear). 'tipo_id' viaja en el mismo PUT -- los
// dos son campos ADMINISTRATIVOS (como seccion_ver), no contenido con flujo
// de borrador, así que comparten el mismo modal en vez de uno cada uno.
let _procCodigoModalActual = null;
function _procEditorCodigoAbrir() {
  if (_procTourActivo) return;
  if (!_procPuedeEditar() || !_procActual || _procActual.error) return;
  _procCerrarCodigoModal();
  const opcionesTipo = (_PROC_TIPOS_CATALOGO || []).map(t => `<option value="${t.id}" ${_procActual.tipo_id === t.id ? 'selected' : ''}>${_esc(t.sigla)} &middot; ${_esc(t.nombre)}</option>`).join('');
  const ov = document.createElement('div');
  ov.className = 'proc-anexo-modal';
  ov.innerHTML = `<div class="proc-anexo-modal-box">
    <div class="card-title">Editar c&oacute;digo y tipo</div>
    <div class="proc-vacio" style="margin-bottom:8px">El c&oacute;digo es el identificador que usan los v&iacute;nculos desde otros procedimientos (anexo tipo "procedimiento") -- si lo cambi&aacute;s, esos v&iacute;nculos se actualizan solos.</div>
    <input class="inp" id="proc-codigo-input" type="text" style="width:100%;margin-bottom:8px" value="${_esc(_procActual.codigo || '')}">
    <select class="inp" id="proc-codigo-tipo" style="width:100%;margin-bottom:8px">
      <option value="">Sin tipo</option>
      ${opcionesTipo}
    </select>
    <div id="proc-codigo-error" style="color:var(--red);font-size:12px;margin-bottom:8px"></div>
    <div style="display:flex;gap:8px">
      <button type="button" class="page-btn" onclick="_procEditorCodigoGuardar()">Guardar</button>
      <button type="button" class="page-btn" onclick="_procCerrarCodigoModal()">Cancelar</button>
    </div>
  </div>`;
  ov.onclick = (e) => { if (e.target === ov) _procCerrarCodigoModal(); };
  document.addEventListener('keydown', function esc(e) { if (e.key === 'Escape') { _procCerrarCodigoModal(); document.removeEventListener('keydown', esc); } });
  document.body.appendChild(ov);
  _procCodigoModalActual = ov;
}
function _procCerrarCodigoModal() {
  if (_procCodigoModalActual) { _procCodigoModalActual.remove(); _procCodigoModalActual = null; }
}
async function _procEditorCodigoGuardar() {
  if (_procTourActivo) return;
  const inp = $id('proc-codigo-input');
  const selTipo = $id('proc-codigo-tipo');
  const err = $id('proc-codigo-error');
  const nuevoCodigo = ((inp && inp.value) || '').trim();
  const nuevoTipoId = (selTipo && selTipo.value) ? Number(selTipo.value) : null;
  if (!nuevoCodigo) { if (err) err.textContent = 'El código no puede estar vacío.'; return; }
  if (nuevoCodigo === _procActual.codigo && nuevoTipoId === (_procActual.tipo_id || null)) { _procCerrarCodigoModal(); return; }
  try {
    const actualizado = await api('/api/procedimientos/' + _procActual.id, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ modificado_en: _procActual.modificado_en, codigo: nuevoCodigo, tipo_id: nuevoTipoId }),
    });
    _procCerrarCodigoModal();
    go('procedimiento_detalle', actualizado.codigo);
  } catch (e) {
    if (err) err.textContent = _procMsgError(e);
  }
}

// ── Tipos de documento (catálogo propio, pedido explícito) ──────────────────
// Crear/modificar/eliminar sus propios tipos (ej. "PE" Procedimiento
// Específico, "PG" Procedimiento General) -- modal aparte, botón en la
// pestaña principal (ver showProcedimientos). Edición inline fila por fila
// (_procTiposEditandoId) en vez de un formulario aparte por fila: son pocas
// filas y cada una es solo sigla+nombre, no amerita la complejidad de un
// editor por bloques como el del documento en sí. Eliminar un tipo en uso
// lo bloquea el backend (409, ver delete_procedimiento_tipo en main.py) --
// acá solo se muestra el mensaje que vuelva.
let _procTiposModalActual = null;
let _procTiposEditandoId = null; // id del tipo en edición inline, o null
let _procTiposMostrarNuevo = false;
function _procTiposAbrir() {
  if (_procTourActivo) return;
  if (!_procPuedeEditar()) return;
  _procCerrarTiposModal();
  _procTiposEditandoId = null;
  _procTiposMostrarNuevo = false;
  const ov = document.createElement('div');
  ov.className = 'proc-anexo-modal';
  ov.innerHTML = `<div class="proc-anexo-modal-box" id="proc-tipos-box">${_procTiposModalHtml()}</div>`;
  ov.onclick = (e) => { if (e.target === ov) _procCerrarTiposModal(); };
  document.addEventListener('keydown', function esc(e) { if (e.key === 'Escape') { _procCerrarTiposModal(); document.removeEventListener('keydown', esc); } });
  document.body.appendChild(ov);
  _procTiposModalActual = ov;
}
function _procCerrarTiposModal() {
  if (_procTiposModalActual) { _procTiposModalActual.remove(); _procTiposModalActual = null; }
}
function _procTiposRefrescar() {
  const box = $id('proc-tipos-box');
  if (box) box.innerHTML = _procTiposModalHtml();
}
function _procTiposEditar(tid) { _procTiposEditandoId = tid; _procTiposRefrescar(); }
function _procTiposCancelarEdicion() { _procTiposEditandoId = null; _procTiposRefrescar(); }
function _procTiposAbrirNuevo() { _procTiposMostrarNuevo = true; _procTiposRefrescar(); }
function _procTiposModalHtml() {
  const filas = (_PROC_TIPOS_CATALOGO || []).map(t => _procTiposEditandoId === t.id ? `<div class="pv-row" style="margin-bottom:4px">
      <input class="inp" id="proc-tipo-edit-sigla" type="text" placeholder="Sigla" style="width:80px" value="${_esc(t.sigla)}">
      <input class="inp" id="proc-tipo-edit-nombre" type="text" placeholder="Nombre" style="flex:1" value="${_esc(t.nombre)}">
      <button type="button" class="page-btn" onclick="_procTiposGuardarEdicion(${t.id})">Guardar</button>
      <button type="button" class="page-btn" onclick="_procTiposCancelarEdicion()">Cancelar</button>
    </div>
    <div id="proc-tipo-edit-error" style="color:var(--red);font-size:12px;margin-bottom:4px"></div>`
    : `<div class="pv-row" style="margin-bottom:4px;align-items:center">
      <span class="proc-codigo-badge">${_esc(t.sigla)}</span>
      <span style="flex:1;font-size:13px">${_esc(t.nombre)}</span>
      <button type="button" class="page-btn" style="padding:2px 8px" onclick="_procTiposEditar(${t.id})" title="Modificar">&#9998;</button>
      <span class="sn-quitar" title="Eliminar tipo" onclick="_procTiposEliminar(${t.id})">&times;</span>
    </div>`
  ).join('');
  const nuevoForm = _procTiposMostrarNuevo
    ? `<div class="pv-row" style="margin-top:8px">
        <input class="inp" id="proc-tipo-nuevo-sigla" type="text" placeholder="Sigla" style="width:80px">
        <input class="inp" id="proc-tipo-nuevo-nombre" type="text" placeholder="Nombre" style="flex:1">
        <button type="button" class="page-btn" onclick="_procTiposCrear()">Agregar</button>
      </div>
      <div id="proc-tipo-nuevo-error" style="color:var(--red);font-size:12px;margin-top:4px"></div>`
    : `<button type="button" class="page-btn" style="margin-top:8px" onclick="_procTiposAbrirNuevo()">&#10133; Agregar tipo</button>`;
  return `<div class="card-title">Tipos de documento</div>
    <div class="proc-vacio" style="margin-bottom:8px">Clasifican cada documento (ej. "PE" Procedimiento Espec&iacute;fico, "PG" Procedimiento General) -- elegibles desde "Nuevo procedimiento" o desde "Editar c&oacute;digo y tipo".</div>
    ${filas || '<div class="proc-vacio">Todav&iacute;a no hay ningun tipo cargado.</div>'}
    ${nuevoForm}
    <div style="margin-top:12px"><button type="button" class="page-btn" onclick="_procCerrarTiposModal()">Cerrar</button></div>`;
}
async function _procTiposCrear() {
  if (_procTourActivo) return;
  const sigla = (($id('proc-tipo-nuevo-sigla') || {}).value || '').trim();
  const nombre = (($id('proc-tipo-nuevo-nombre') || {}).value || '').trim();
  const err = $id('proc-tipo-nuevo-error');
  if (!sigla || !nombre) { if (err) err.textContent = 'Hace falta la sigla y el nombre.'; return; }
  try {
    const creado = await api('/api/procedimientos/tipos', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ sigla, nombre }) });
    _PROC_TIPOS_CATALOGO = (_PROC_TIPOS_CATALOGO || []).concat([creado]).sort((a, b) => a.sigla.localeCompare(b.sigla));
    _procTiposMostrarNuevo = false;
    _procTiposRefrescar();
  } catch (e) {
    if (err) err.textContent = _procMsgError(e);
  }
}
async function _procTiposGuardarEdicion(tid) {
  if (_procTourActivo) return;
  const sigla = (($id('proc-tipo-edit-sigla') || {}).value || '').trim();
  const nombre = (($id('proc-tipo-edit-nombre') || {}).value || '').trim();
  const err = $id('proc-tipo-edit-error');
  if (!sigla || !nombre) { if (err) err.textContent = 'Hace falta la sigla y el nombre.'; return; }
  try {
    const actualizado = await api('/api/procedimientos/tipos/' + tid, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ sigla, nombre }) });
    const idx = (_PROC_TIPOS_CATALOGO || []).findIndex(t => t.id === tid);
    if (idx !== -1) _PROC_TIPOS_CATALOGO[idx] = actualizado;
    _procTiposEditandoId = null;
    _procTiposRefrescar();
  } catch (e) {
    if (err) err.textContent = _procMsgError(e);
  }
}
async function _procTiposEliminar(tid) {
  if (_procTourActivo) return;
  if (!confirm('¿Eliminar este tipo de documento? No se puede deshacer.')) return;
  try {
    await api('/api/procedimientos/tipos/' + tid, { method: 'DELETE' });
    _PROC_TIPOS_CATALOGO = (_PROC_TIPOS_CATALOGO || []).filter(t => t.id !== tid);
    _procTiposRefrescar();
  } catch (e) {
    alert('No se pudo eliminar: ' + _procMsgError(e));
  }
}

// ── Recorrido guiado ("Ayuda") ──────────────────────────────────────────────
// Mismo motor genérico que usan Cargar moldeo y Trabajos (_iniciarTour, en
// index.html) -- acá solo se define QUÉ mostrar. Todo lo que el recorrido
// pinta (la fila de la lista, el documento, el borrador, el cartel de
// aprobación) es un documento de EJEMPLO armado en memoria -- nunca toca la
// base. _procTourActivo es la bandera que lo marca mientras corre: hace que
// _procPuedeEditar() siempre devuelva true (para poder mostrar la vista
// completa a cualquiera, mire quien mire) y bloquea como segunda barrera
// cada función que de verdad llama a la api (ver los "if (_procTourActivo)
// return" repartidos arriba) -- la primera barrera es el overlay del
// recorrido, que ya bloquea los clics de fondo.
let _procTourActivo = false;
const _PROC_TOUR_CODIGO = '__recorrido__';
function _procTourDocPublicado() {
  return {
    id: -1, codigo: _PROC_TOUR_CODIGO, titulo: 'Encendido del horno (ejemplo)',
    revision: 2, seccion_ver: 'sinterizado', estado: 'activo', error: null,
    borrador_estado: null, borrador_titulo: null, borrador_contenido: null, borrador_motivo: null,
    borrador_por_nombre: null, borrador_por_legajo: null, borrador_enviado_en: null,
    modificado_por_nombre: 'Ana Fusión', modificado_en: '2026-09-15T09:00:00',
    contenido: { blocks: [
      { type: 'header', data: { text: 'Antes de encender', level: 2 } },
      { type: 'ficha', data: { titulo: 'Datos', filas: [['Área', 'Fundición'], ['Frecuencia', 'Diaria']] } },
      { type: 'pasos', data: { titulo: 'Pasos', pasos: [
        { titulo: 'Revisar el nivel de refractario', detalles: ['Controlar que no haya grietas visibles'], nota: '', archivos: [] },
      ] } },
    ] },
  };
}
// autorSoyYo=true arma el borrador a nombre de quien está mirando el
// recorrido (para mostrar "lo mandaste vos, no lo podés aprobar"); false lo
// arma a nombre de alguien inventado (para mostrar la vista de un tercero,
// con Aprobar/Rechazar disponibles) -- el mismo borrador, dos perspectivas.
function _procTourDocPendiente(autorSoyYo) {
  const base = _procTourDocPublicado();
  base.borrador_estado = 'pendiente_aprobacion';
  base.borrador_motivo = 'Se agregó una advertencia sobre la temperatura mínima antes de cargar.';
  base.borrador_por_nombre = autorSoyYo ? ((_AUTH.user && _AUTH.user.nombre) || 'Vos') : 'Beto Larreta';
  base.borrador_por_legajo = autorSoyYo ? (_AUTH.user && _AUTH.user.legajo) : -999;
  base.borrador_enviado_en = '2026-10-01T11:20:00';
  return base;
}
function _procTourDocAprobado() {
  const base = _procTourDocPublicado();
  base.revision = 3;
  base.modificado_por_nombre = (_AUTH.user && _AUTH.user.nombre) || 'Quien aprobó';
  base.modificado_en = '2026-10-01T11:25:00';
  base.contenido.blocks.push({ type: 'ficha', data: { titulo: 'Advertencia', filas: [['Temperatura mínima', '950°C']] } });
  return base;
}
function _procTourEditorDemo() {
  const doc = _procTourDocPublicado();
  return { titulo: doc.titulo, blocks: JSON.parse(JSON.stringify(doc.contenido.blocks)) };
}
function _procTourFilaDemo() {
  return { codigo: _PROC_TOUR_CODIGO, titulo: 'Encendido del horno (ejemplo)', seccion_ver: 'sinterizado', modificado_por_nombre: 'Ana Fusión', modificado_en: '2026-09-15T09:00:00' };
}
// Reemplaza #proc-principal por el mismo armado que _procAbrirDesdeLista (sin
// pasar por la api ni por go(), para quedarnos en la misma pantalla) -- los
// botones de volver/eliminar van SIN onclick a propósito: son parte del
// decorado (para que se vea igual a la pantalla real), nunca hace falta que
// hagan algo de verdad, ni siquiera con el overlay bloqueando el clic.
function _procTourMostrarDetalle(doc) {
  const cont = $id('proc-principal');
  if (!cont) return;
  cont.innerHTML = `<div class="sec-title">${_esc(doc.titulo)} ${_procCodigoBadgeHtml(doc.codigo)}</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <button type="button" class="page-btn">&larr; Volver a Procedimientos</button>
      <button type="button" class="page-btn" style="color:var(--red)">&#128465; Eliminar procedimiento</button>
    </div>
    <div id="proc-pagina" style="margin-top:12px"></div>`;
  _procRegistrar(doc, 'proc-pagina', null);
}
// Se llama en CADA paso (lo mismo que _moldeoTourSincronizarModal) -- nunca
// arrastra el estado del paso anterior: decide todo desde cero a partir de
// las banderas de paso.pantalla/doc/editor/sucio/modalEnviar/formNuevo.
async function _procTourSincronizar(paso) {
  _procCerrarEnviarModal();
  _procFormNuevo = null;
  if (paso.pantalla === 'lista') {
    _procPintarLista();
    if (paso.formNuevo) {
      _procAbrirFormNuevo();
      if (paso.formCampo) _procFormNuevoCampo(paso.formCampo[0], paso.formCampo[1]);
    }
    return;
  }
  _procTourMostrarDetalle(paso.doc ? paso.doc() : _procTourDocPublicado());
  _procEditor = paso.editor ? _procTourEditorDemo() : null;
  _procBorradorModificadoEn = _procEditor ? '2026-10-01T00:30:00' : null;
  _procRefrescarse();
  if (paso.sucio) _procEstadoGuardado(paso.sucio.html, paso.sucio.color);
  if (paso.modalEnviar) _procEditorGuardar(); // valida y abre el modal de verdad -- no llama a la api
}
const _PROC_TOUR_PASOS = [
  { pantalla: 'lista', buscar: () => document.querySelector('#proc-principal .tbl-wrap'),
    titulo: 'La lista', texto: 'Esta pantalla lista TODOS los procedimientos que pod&eacute;s ver, de cualquier m&oacute;dulo del sistema &mdash; hoy el &uacute;nico que existe de verdad es el del horno de sinterizado, pero cualquier pantalla puede colgar el suyo sin pedir nada nuevo.' },
  { pantalla: 'lista', buscar: () => document.querySelector('#proc-principal tr[onclick*="__recorrido__"]'),
    titulo: 'Cada fila', texto: 'T&iacute;tulo, la secci&oacute;n que hace falta tener para VERLO (o <b>&mdash;</b> si no pide ninguna) y qui&eacute;n lo toc&oacute; por &uacute;ltimo. Un click lleva al detalle.' },
  { pantalla: 'lista', sel: 'button[onclick="_procAbrirFormNuevo()"]',
    titulo: 'Nuevo procedimiento', texto: 'Este bot&oacute;n solo lo ven quienes tienen el permiso <b>procedimientos_editar</b> (o son administradores) &mdash; es el &uacute;nico permiso que hace falta para crear, modificar, aprobar, rechazar o eliminar <b>cualquier</b> procedimiento, sin importar de qu&eacute; m&oacute;dulo sea.' },
  { pantalla: 'lista', formNuevo: true, buscar: () => document.querySelector('#proc-principal input[placeholder="Código (identificador único, sin espacios)"]'),
    titulo: 'C&oacute;digo', texto: 'Un identificador &uacute;nico y estable, sin espacios &mdash; es la clave que usa cada m&oacute;dulo consumidor para pedir este documento (hoy, <b>sinterizado_pg85106</b>). No se puede repetir.' },
  { pantalla: 'lista', formNuevo: true, buscar: () => document.querySelector('#proc-principal input[placeholder="Título"]'),
    titulo: 'T&iacute;tulo', texto: 'El nombre visible en la lista y en el detalle. A diferencia del c&oacute;digo, se puede corregir despu&eacute;s &mdash; como cualquier otro contenido, pasando por un borrador y una aprobaci&oacute;n (ya lo vemos).' },
  { pantalla: 'lista', formNuevo: true, buscar: () => document.querySelector('#proc-principal .proc-anexo-form select'),
    titulo: 'Qu&eacute; secci&oacute;n lo puede ver', texto: 'Cualquiera con este permiso de secci&oacute;n va a poder VER el documento publicado, aunque no pueda editarlo. Sin elegir ninguna, solo lo ven quienes editan procedimientos.' },
  { pantalla: 'lista', formNuevo: true, buscar: () => document.querySelector('#proc-principal input[placeholder="Revisión inicial"]'),
    titulo: 'Revisi&oacute;n inicial', texto: 'Un n&uacute;mero entero de 0 a 999. Pensado para dar de alta procedimientos que en Access ya ten&iacute;an una revisi&oacute;n impresa, sin perder esa numeraci&oacute;n &mdash; de ac&aacute; en adelante sube sola +1 cada vez que se aprueba un reemplazo.' },
  { pantalla: 'lista', formNuevo: true, sel: '#proc-nuevo-guardar',
    titulo: 'Crear', texto: 'Crea el documento vac&iacute;o y lleva directo a su detalle. Todav&iacute;a no hay nada que aprobar &mdash; el primer contenido de un documento nuevo se agrega igual que cualquier cambio despu&eacute;s: por un borrador.' },

  { pantalla: 'detalle', doc: _procTourDocPublicado, buscar: () => document.querySelector('#proc-pagina > div:first-child'),
    titulo: 'Rev.002', texto: 'El n&uacute;mero de revisi&oacute;n, siempre visible arriba de todo. Sube +1 cada vez que se aprueba un borrador que reemplaza esta versi&oacute;n &mdash; nunca se toca a mano.' },
  { pantalla: 'detalle', doc: _procTourDocPublicado, buscar: () => document.querySelector('#proc-pagina .card.sn-card'),
    titulo: 'El contenido publicado', texto: 'Esto es lo que ve cualquiera con el permiso de secci&oacute;n de este documento. Est&aacute; armado por bloques &mdash; encabezados, fichas, pasos numerados, tablas, im&aacute;genes, gr&aacute;ficos y anexos &mdash; cada uno independiente del resto.' },
  { pantalla: 'detalle', doc: _procTourDocPublicado, sel: 'button[onclick="_procAbrirEditor()"]',
    titulo: 'Modificar', texto: 'Solo la ven quienes editan procedimientos. Al tocarlo se abre un <b>borrador</b> en el servidor &mdash; una copia aparte que convive con lo publicado sin tocarlo. Qui&eacute;n solo puede VER el documento no nota nada todav&iacute;a.' },

  { pantalla: 'detalle', doc: _procTourDocPublicado, editor: true, buscar: () => document.querySelector('#proc-pagina input[placeholder="Título"]'),
    titulo: 'El borrador', texto: 'Este es el editor, con una copia editable del contenido &mdash; lo publicado (Rev.002, lo que vimos reci&eacute;n) sigue intacto en paralelo. Se puede cerrar y volver cuando quieras: el borrador qued&oacute; guardado en el servidor, no en esta pantalla.' },
  { pantalla: 'detalle', doc: _procTourDocPublicado, editor: true,
    buscar: () => [document.querySelector('button[onclick="_procEditorMover(0,-1)"]'), document.querySelector('button[onclick="_procEditorMover(0,1)"]'), document.querySelector('span[onclick="_procEditorEliminarBloque(0)"]')],
    titulo: 'Reordenar y eliminar', texto: 'Cada bloque tiene sus propias flechas &#9650;&#9660; para moverlo, y la &times; para sacarlo del documento. Nada de esto toca lo publicado todav&iacute;a &mdash; es parte del borrador.' },
  { pantalla: 'detalle', doc: _procTourDocPublicado, editor: true,
    buscar: () => Array.from(document.querySelectorAll('button[onclick^="_procEditorAgregarBloque"]')),
    titulo: 'Agregar un bloque', texto: 'Nueve tipos: encabezado, texto, lista, tabla, ficha (pares clave/valor), pasos numerados (cada uno con su propia nota y archivos), imagen, anexo (un archivo bajo una palabra clave que se enlaza desde cualquier texto) y gr&aacute;fico.' },
  { pantalla: 'detalle', doc: _procTourDocPublicado, editor: true, sucio: { html: '&#9679; cambios sin guardar', color: 'var(--yellow)' }, sel: '#proc-sucio',
    titulo: 'Autoguardado', texto: 'Cada cambio programa un guardado del borrador 1,2 segundos despu&eacute;s &mdash; mientras tanto, este indicador avisa que hay algo sin guardar.' },
  { pantalla: 'detalle', doc: _procTourDocPublicado, editor: true, sucio: { html: '&#9679; guardando...', color: 'var(--muted)' }, sel: '#proc-sucio',
    titulo: 'Guardando...', texto: 'El PUT del borrador viaj&oacute; al servidor. Si choca con una versi&oacute;n m&aacute;s nueva (otra pesta&ntilde;a abierta, por ejemplo) reintenta solo, hasta 3 veces, sin interrumpir a qui&eacute;n est&aacute; escribiendo.' },
  { pantalla: 'detalle', doc: _procTourDocPublicado, editor: true, sucio: { html: 'Guardado autom&aacute;ticamente', color: 'var(--green)' }, sel: '#proc-sucio',
    titulo: 'Guardado autom&aacute;ticamente', texto: 'Si falla por otro motivo (sin conexi&oacute;n, por ejemplo) lo vuelve a intentar a los 8 segundos, en vez de avisar y quedarse sin guardar. Nada de esto se pierde aunque se cierre la pesta&ntilde;a a mitad de edici&oacute;n.' },
  { pantalla: 'detalle', doc: _procTourDocPublicado, editor: true, sel: 'button[onclick="_procDescartarBorrador()"]',
    titulo: 'Descartar el borrador', texto: 'A diferencia de aprobar o rechazar (ya los vemos), descartar <b>no</b> necesita que otra persona intervenga &mdash; lo puede hacer el propio autor en cualquier momento. Se pierde lo que se hab&iacute;a escrito ac&aacute;; lo publicado no se toca.' },
  { pantalla: 'detalle', doc: _procTourDocPublicado, editor: true, sel: 'button[onclick="_procEditorGuardar()"]',
    titulo: 'Guardar', texto: 'Antes de nada, valida: t&iacute;tulo obligatorio, y si hay anexos, cada uno con un nombre &uacute;nico. Si todo est&aacute; bien, no guarda directo &mdash; pide explicar el cambio.' },

  { pantalla: 'detalle', doc: _procTourDocPublicado, editor: true, modalEnviar: true, sel: '#proc-motivo-envio',
    titulo: 'Explicar el cambio', texto: 'Obligatorio. Ese texto queda guardado junto con el borrador y lo va a leer quien tenga que aprobarlo o rechazarlo &mdash; es el &uacute;nico lugar donde consta <b>por qu&eacute;</b> cambi&oacute; algo, no solo qu&eacute;.' },
  { pantalla: 'detalle', doc: _procTourDocPublicado, editor: true, modalEnviar: true, sel: '#proc-enviar-confirmar',
    titulo: 'Enviar a aprobaci&oacute;n', texto: 'Confirmar hace dos cosas: un &uacute;ltimo guardado del borrador (por si el autoguardado todav&iacute;a no corri&oacute;) y, reci&eacute;n despu&eacute;s, lo manda a <b>pendiente de aprobaci&oacute;n</b>. Deja de ser editable hasta que alguien lo resuelva.' },

  { pantalla: 'detalle', doc: () => _procTourDocPendiente(true), buscar: () => document.querySelector('#proc-pagina > div:last-child'),
    titulo: 'Pendiente de aprobaci&oacute;n', texto: 'Mientras espera, <b>cualquiera</b> que pueda ver el documento ve este cartel &mdash; qui&eacute;n lo mand&oacute; y por qu&eacute; &mdash; aunque todav&iacute;a no vea el contenido nuevo. Es transparencia: hay un cambio en camino.' },
  { pantalla: 'detalle', doc: () => _procTourDocPendiente(true), sel: 'button[onclick="_procDescartarBorrador()"]',
    titulo: 'El propio autor no puede resolverlo', texto: 'Si qui&eacute;n mand&oacute; el borrador es quien est&aacute; mirando (como ac&aacute;), no hay bot&oacute;n de Aprobar ni de Rechazar &mdash; ni siquiera si es administrador. Solo puede descartarlo.' },

  { pantalla: 'detalle', doc: () => _procTourDocPendiente(false),
    buscar: () => [document.querySelector('button[onclick="_procAprobarBorrador()"]'), document.querySelector('button[onclick="_procRechazarBorrador()"]')],
    titulo: 'Otra persona, con permiso', texto: 'Para cualquier otra persona que edite procedimientos, el mismo borrador se ve as&iacute;: con Aprobar y Rechazar disponibles. <b>Aprobar</b> reemplaza lo publicado, sube la revisi&oacute;n y archiva la versi&oacute;n vieja en el historial. <b>Rechazar</b> lo devuelve a edici&oacute;n para que el autor lo corrija &mdash; lo publicado no cambia en ning&uacute;n caso hasta que se aprueba.' },

  { pantalla: 'detalle', doc: _procTourDocAprobado, buscar: () => document.querySelector('#proc-pagina > div:first-child'),
    titulo: 'Rev.003', texto: 'As&iacute; queda despu&eacute;s de aprobar: la revisi&oacute;n subi&oacute; sola, el contenido del borrador pas&oacute; a ser lo publicado, y la Rev.002 de reci&eacute;n qued&oacute; guardada entera en el historial &mdash; con el motivo del cambio que la reemplaz&oacute;.' },
];
function _procIniciarTour() {
  _procTourActivo = true;
  // Si se toca "Ayuda" mientras la lista real todavía está cargando,
  // _PROC_LISTA puede ser null -- igual tiene que haber algo para señalar en
  // el paso de "cada fila", así que arranca de una lista vacía si hace falta.
  if (!Array.isArray(_PROC_LISTA)) _PROC_LISTA = [];
  if (!_PROC_LISTA.some(p => p.codigo === _PROC_TOUR_CODIGO)) {
    _PROC_LISTA = [_procTourFilaDemo(), ..._PROC_LISTA];
  }
  _iniciarTour(_PROC_TOUR_PASOS, {
    sincronizar: _procTourSincronizar,
    onClose: () => {
      _procTourActivo = false;
      _procCerrarEnviarModal();
      _procFormNuevo = null;
      _procEditor = null;
      _procActual = null;
      _procContenedorId = null;
      showProcedimientos(); // recarga la lista de verdad, sin la fila de ejemplo
    },
  });
}
