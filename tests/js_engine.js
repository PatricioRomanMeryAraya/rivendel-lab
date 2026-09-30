#!/usr/bin/env node
// Puente de test: extrae el motor de pricing del frontend (web/index.html) y
// evalúa una lista de casos JSON, devolviendo precios y griegas CRUDAS
// (vega por 1.00 de vol, theta por año, rho por 1.00). Lo usa la suite de
// Python para comparar el motor JS contra una referencia independiente.
//
// Uso:  node js_engine.js casos.json   (imprime un array JSON por stdout)
const fs = require('fs');
const path = require('path');
const HTML = process.env.RIVENDEL_HTML || path.join(__dirname, '..', 'web', 'index.html');
const html = fs.readFileSync(HTML, 'utf8');
const s = html.indexOf('function erf(');
const e = html.indexOf('/* IV que usa una pata');
if (s < 0 || e < 0) { console.error('no encuentro el bloque de motor en index.html'); process.exit(2); }
// Slice autocontenido: erf, bsOne, pvDivs, amPrice, amGreeks, euPriceDiv (+ superficie,
// que sólo se define, no se llama). Los references a MK/RATE/SURF se resuelven en runtime.
eval(html.slice(s, html.lastIndexOf('}', e) + 1) + '\nglobalThis.__E = { bsOne, amPrice, amGreeks };');
const E = globalThis.__E;
const gk = g => ({ price: g.price, delta: g.delta, gamma: g.gamma, vega: g.vega, theta: g.theta, rho: g.rho });
const cases = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
process.stdout.write(JSON.stringify(cases.map(c => {
  if (c.op === 'bs') return gk(E.bsOne(c.kind, c.S, c.K, c.T, c.sig, c.r, c.q || 0));
  if (c.op === 'am') return { price: E.amPrice(c.kind, c.S, c.K, c.T, c.sig, c.r, c.q || 0, c.N || 200, c.divs || []) };
  if (c.op === 'amgreeks') return gk(E.amGreeks(c.kind, c.S, c.K, c.T, c.sig, c.r, c.q || 0, c.divs || []));
  return {};
})));
