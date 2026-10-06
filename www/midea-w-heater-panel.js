const CHART_DAYS = 3;
const CHART_MS = CHART_DAYS * 24 * 60 * 60 * 1000;
const DAY_MS = 24 * 60 * 60 * 1000;
const RATE_COLORS = {
  peak: "rgba(226, 74, 74, 0.28)",
  shoulder: "rgba(232, 119, 34, 0.18)",
  low: "rgba(61, 204, 110, 0.16)",
};
const RATE_LEGEND = [
  { name: "Peak 4pm–9pm", color: "rgba(226, 74, 74, 0.7)" },
  { name: "Shoulder 9pm–11am", color: "rgba(232, 119, 34, 0.7)" },
  { name: "Low 11am–4pm", color: "rgba(61, 204, 110, 0.7)" },
];
const MODE_SHORT = {
  off: "OFF",
  eco: "ECO",
  performance: "HYBRID",
  electric: "E-HEAT",
};

class MideaWHeaterPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._resizeObserver = null;
    this._shell = false;
    this._chartWidth = 0;
  }

  static getStubConfig() {
    return {
      title: "Midea W-Heater",
      water_heater: "water_heater.chromagen_hp170",
      sensors: [
        { name: "T5L", entity: "sensor.chromagen_hp170_t5_tank", color: "#7dff9a" },
        { name: "T3 Evaporator", entity: "sensor.chromagen_hp170_t3_evaporator", color: "#e87722" },
        { name: "T4 Ambient", entity: "sensor.chromagen_hp170_t4_ambient", color: "#8ec8ff" },
        { name: "TP Discharge", entity: "sensor.chromagen_hp170_tp_discharge_air", color: "#ff8fab" },
        { name: "EEV", entity: "sensor.chromagen_hp170_eev", color: "#e8d48a", unit: "", digits: 0, chart: false },
      ],
    };
  }

  setConfig(config) {
    if (!config.water_heater) {
      throw new Error("Set a water_heater entity");
    }
    this.config = {
      title: config.title || "Midea W-Heater",
      water_heater: config.water_heater,
      sensors: config.sensors || [],
    };
    this._sig = "";
    this._chartSeries = [];
    this._powerSeries = [];
    this._modeSeries = [];
    this._chartLoadedAt = 0;
    this._chartLoading = false;
    this._shell = false;
    this._chartWidth = 0;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._shell) {
      this._buildShell();
    }
    const sig = this._signature();
    if (sig !== this._sig) {
      this._sig = sig;
      this._paintLcd();
    }
    this._ensureChart();
  }

  disconnectedCallback() {
    if (this._resizeObserver) {
      this._resizeObserver.disconnect();
      this._resizeObserver = null;
    }
  }

  getCardSize() {
    return 10;
  }

  _state(entityId) {
    return entityId ? this._hass?.states[entityId] : undefined;
  }

  _num(stateObj) {
    if (!stateObj || stateObj.state === "unavailable" || stateObj.state === "unknown") {
      return null;
    }
    const value = Number(stateObj.state);
    return Number.isFinite(value) ? value : null;
  }

  _fmt(value, digits) {
    if (value == null || Number.isNaN(Number(value))) {
      return "—";
    }
    return Number(value).toFixed(digits);
  }

  _modeLabel(mode) {
    return MODE_SHORT[mode] || (mode ? String(mode).toUpperCase() : "—");
  }

  _lcdModel() {
    const wh = this._state(this.config.water_heater);
    const missing = !wh;
    const mode = wh?.state || "unavailable";
    const stored = String(wh?.attributes?.mode_label || "").toLowerCase();
    const displayMode =
      mode === "off" && stored && stored !== "off"
        ? stored === "e-heater"
          ? "electric"
          : stored === "hybrid"
            ? "performance"
            : stored
        : mode;
    const on = mode !== "off" && mode !== "unavailable" && mode !== "unknown";
    const timer = Boolean(wh?.attributes?.timer);
    const target = wh?.attributes?.temperature;
    const tankSensor = this.config.sensors.find((sensor) =>
      String(sensor.name || "").toUpperCase().startsWith("T5")
    );
    const tankFromSensor = tankSensor ? this._num(this._state(tankSensor.entity)) : null;
    const tank = tankFromSensor != null ? tankFromSensor : wh?.attributes?.current_temperature;
    const min = wh?.attributes?.min_temp;
    const max = wh?.attributes?.max_temp;
    const limits =
      min != null && max != null ? `${this._fmt(min, 0)}–${this._fmt(max, 0)}°C` : "—";
    const clock = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    return { wh, missing, mode, displayMode, on, timer, target, tank, limits, clock };
  }

  _signature() {
    const model = this._lcdModel();
    const sensors = this.config.sensors
      .map((sensor) => this._state(sensor.entity)?.state || "")
      .join("|");
    return `${model.mode}|${model.displayMode}|${model.target || ""}|${model.timer}|${model.tank}|${sensors}|${model.clock}`;
  }

  _buildShell() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card {
          --ha-card-background: #1a1f24;
          --primary-text-color: #e8eef2;
          color: #e8eef2;
          overflow: hidden;
        }
        .wrap { padding: 16px; display: grid; gap: 16px; }
        .banner { padding: 8px 12px; border-radius: 8px; font-size: 0.9rem; }
        .banner.ok { background: #16351f; color: #3dcc6e; }
        .banner.fail { background: #3a1518; color: #e24a4a; }
        .unit {
          background: linear-gradient(#2b3238, #1c2228);
          border-radius: 16px;
          padding: 18px;
        }
        .bezel { background: #111; border-radius: 10px; padding: 14px; }
        .lcd {
          background: #0a1a12;
          color: #7dff9a;
          font-family: Consolas, "Cascadia Mono", monospace;
          border-radius: 6px;
          padding: 16px 18px;
          letter-spacing: 0.04em;
        }
        .row { display: flex; justify-content: space-between; gap: 12px; margin-bottom: 6px; }
        .big { font-size: 2.4rem; line-height: 1.1; }
        .dim { color: #3a6b48; }
        .temps {
          display: grid;
          grid-template-columns: repeat(3, 1fr);
          gap: 8px;
          margin-top: 12px;
          font-family: Consolas, monospace;
          font-size: 0.82rem;
          color: #8a97a3;
        }
        .set { font-size: 1.05rem; letter-spacing: 0.04em; }
        .temps div { background: #161c20; padding: 8px 10px; border-radius: 6px; }
        .temps b { color: #d6e2ea; display: block; font-size: 1rem; }
        .keys { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-top: 14px; }
        button {
          background: #2a333c;
          color: #e8eef2;
          border: 0;
          border-radius: 8px;
          padding: 12px 8px;
          font-size: 0.9rem;
        }
        button.on { background: #e87722; color: #111; font-weight: 600; }
        button:disabled { opacity: 1; cursor: default; }
        button:disabled:not(.on) { opacity: 0.7; }
        .note { color: #8a97a3; font-size: 0.85rem; margin: 10px 0 0; }
        .chart-title { font-size: 1rem; font-weight: 600; margin: 4px 0 8px; }
        canvas {
          width: 100%;
          height: 240px;
          display: block;
          background: #111;
          border-radius: 10px;
        }
        .legend { display: flex; flex-wrap: wrap; gap: 10px 14px; margin-top: 8px; font-size: 0.82rem; color: #8a97a3; }
        .swatch { width: 12px; height: 12px; border-radius: 2px; display: inline-block; margin-right: 6px; vertical-align: -1px; }
        .swatch.midnight { width: 2px; background: #c9d6de; border-radius: 0; }
        .swatch.noon {
          width: 0;
          height: 12px;
          background: transparent;
          border-left: 2px dotted #c9d6de;
          border-radius: 0;
        }
        .swatch.mode {
          width: 0;
          height: 12px;
          background: transparent;
          border-left: 2px dashed #ffe08a;
          border-radius: 0;
        }
        @media (max-width: 640px) {
          .temps, .keys { grid-template-columns: repeat(2, 1fr); }
          .big { font-size: 2rem; }
        }
      </style>
      <ha-card>
        <div class="wrap">
          <div id="banner" class="banner ok"></div>
          <div class="unit">
            <div class="bezel">
              <div class="lcd">
                <div class="row dim"><span>MIDEA 170L · T5</span><span id="clk">--:--</span></div>
                <div class="row"><span class="big" id="tank">--</span><span id="modeLine">—</span></div>
                <div class="row"><span class="set" id="setLine">SET —</span><span id="powerLine">POWER --</span></div>
                <div class="row dim"><span id="limitLine">LIMITS —</span></div>
              </div>
              <div class="temps" id="temps"></div>
            </div>
            <div class="keys" id="keys"></div>
            <p class="note">Keys follow the water heater state. This panel does not send commands.</p>
          </div>
          <div>
            <div class="chart-title">Temperatures · ${CHART_DAYS} days</div>
            <canvas id="temp-chart"></canvas>
            <div class="legend" id="chart-legend"></div>
          </div>
        </div>
      </ha-card>
    `;
    this._shell = true;
    const canvas = this.shadowRoot.querySelector("#temp-chart");
    if (this._resizeObserver) {
      this._resizeObserver.disconnect();
    }
    this._resizeObserver = new ResizeObserver(() => {
      this._drawChart(true);
    });
    if (canvas) {
      this._resizeObserver.observe(canvas);
    }
  }

  _paintLcd() {
    const model = this._lcdModel();
    const banner = this.shadowRoot.getElementById("banner");
    if (model.missing) {
      banner.className = "banner fail";
      banner.textContent = "Water heater entity not found";
    } else if (model.mode === "unavailable") {
      banner.className = "banner fail";
      banner.textContent = "Water heater unavailable";
    } else {
      banner.className = "banner ok";
      banner.textContent = this.config.title;
    }
    this.shadowRoot.getElementById("clk").textContent = model.clock;
    this.shadowRoot.getElementById("tank").textContent =
      model.tank == null ? "--" : `${this._fmt(model.tank, 1)}°`;
    this.shadowRoot.getElementById("modeLine").textContent = this._modeLabel(model.displayMode);
    this.shadowRoot.getElementById("setLine").textContent =
      model.target == null ? "SET —" : `SET ${this._fmt(model.target, 0)}°`;
    const powerBits = [model.on ? "POWER ON" : "POWER OFF", this._modeLabel(model.displayMode)];
    if (model.timer) {
      powerBits.push("TIMER");
    }
    this.shadowRoot.getElementById("powerLine").textContent = powerBits.join(" · ");
    this.shadowRoot.getElementById("limitLine").textContent = `LIMITS ${model.limits}`;
    this.shadowRoot.getElementById("temps").innerHTML = this.config.sensors
      .map((sensor) => {
        const value = this._num(this._state(sensor.entity));
        const digits = sensor.digits == null ? 1 : sensor.digits;
        const unit = Object.prototype.hasOwnProperty.call(sensor, "unit")
          ? sensor.unit
          : " °C";
        const text = value == null ? "—" : `${value.toFixed(digits)}${unit}`;
        return `<div>${sensor.name}<b>${text}</b></div>`;
      })
      .join("");
    this.shadowRoot.getElementById("keys").innerHTML = [
      ["Power", model.on],
      ["Eco", model.on && model.displayMode === "eco"],
      ["Hybrid", model.on && model.displayMode === "performance"],
      ["E-Heater", model.on && model.displayMode === "electric"],
    ]
      .map(
        ([label, active]) =>
          `<button type="button" class="${active ? "on" : ""}" disabled>${label}</button>`
      )
      .join("");
  }

  _chartEntities() {
    return this.config.sensors
      .filter((sensor) => sensor.chart !== false && sensor.entity)
      .map((sensor) => sensor.entity);
  }

  _ensureChart() {
    const canvas = this.shadowRoot?.querySelector("#temp-chart");
    if (!canvas || !this._hass || this._chartLoading) {
      return;
    }
    if (!this._chartLoadedAt || Date.now() - this._chartLoadedAt > 300000) {
      this._loadChartHistory();
      return;
    }
  }

  async _loadChartHistory() {
    if (!this._hass || this._chartLoading) {
      return;
    }
    this._chartLoading = true;
    const end = new Date();
    const start = new Date(end.getTime() - CHART_MS);
    const sensors = this._chartEntities();
    const entities = this.config.water_heater
      ? [...sensors, this.config.water_heater]
      : sensors;
    try {
      const history = await this._hass.callWS({
        type: "history/history_during_period",
        start_time: start.toISOString(),
        end_time: end.toISOString(),
        entity_ids: entities,
        significant_changes_only: false,
        minimal_response: true,
        no_attributes: true,
      });
      const map = this._historyMap(history, entities);
      this._chartSeries = this.config.sensors
        .filter((sensor) => sensor.chart !== false && sensor.entity)
        .map((sensor, index) => ({
          name: sensor.name,
          color: sensor.color || ["#7dff9a", "#3dcc6e", "#e87722", "#8ec8ff", "#ff8fab"][index % 5],
          points: this._points(map[sensor.entity] || [], true),
        }));
      this._modeSeries = this._modePoints(map[this.config.water_heater] || []);
      this._powerSeries = this._modeSeries.map((point) => ({
        time: point.time,
        value: point.mode === "off" ? 0 : 1,
      }));
      this._chartLoadedAt = Date.now();
      this._drawChart(true);
    } catch (error) {
      console.warn("Midea W-Heater chart history failed", error);
    } finally {
      this._chartLoading = false;
    }
  }

  _historyMap(response, entityIds) {
    if (!response) {
      return {};
    }
    if (Array.isArray(response)) {
      const map = {};
      entityIds.forEach((entityId, index) => {
        map[entityId] = response[index] || [];
      });
      return map;
    }
    return response;
  }

  _timeMs(row) {
    if (row.lu != null) {
      const lu = Number(row.lu);
      if (Number.isFinite(lu)) {
        return lu < 1e12 ? lu * 1000 : lu;
      }
    }
    const raw = row.last_updated || row.last_changed;
    const parsed = raw ? Date.parse(raw) : NaN;
    return Number.isFinite(parsed) ? parsed : NaN;
  }

  _points(rows, numeric) {
    return (rows || [])
      .map((row) => {
        const time = this._timeMs(row);
        if (!Number.isFinite(time)) {
          return null;
        }
        const raw = row.s ?? row.state;
        if (raw === "unavailable" || raw === "unknown" || raw == null) {
          return null;
        }
        if (numeric) {
          const value = Number(raw);
          return Number.isFinite(value) ? { time, value } : null;
        }
        return { time, value: raw === "off" ? 0 : 1 };
      })
      .filter(Boolean)
      .sort((a, b) => a.time - b.time);
  }

  _modePoints(rows) {
    return (rows || [])
      .map((row) => {
        const time = this._timeMs(row);
        const raw = row.s ?? row.state;
        if (!Number.isFinite(time) || raw == null || raw === "unavailable" || raw === "unknown") {
          return null;
        }
        return { time, mode: String(raw) };
      })
      .filter(Boolean)
      .sort((a, b) => a.time - b.time);
  }

  _modeMarks() {
    const marks = [];
    let previous = null;
    for (const point of this._modeSeries) {
      if (previous != null && point.mode !== previous) {
        marks.push(point);
      }
      previous = point.mode;
    }
    return marks;
  }

  _powerBands(end) {
    const bands = [];
    let from = null;
    for (const point of this._powerSeries) {
      if (point.value === 1) {
        if (from == null) {
          from = point.time;
        }
      } else if (from != null) {
        bands.push({ from, to: point.time });
        from = null;
      }
    }
    if (from != null) {
      bands.push({ from, to: end });
    }
    return bands;
  }

  _drawChart(force) {
    const canvas = this.shadowRoot?.querySelector("#temp-chart");
    const legend = this.shadowRoot?.querySelector("#chart-legend");
    if (!canvas) {
      return;
    }
    const width = Math.floor(canvas.clientWidth);
    if (width < 80) {
      return;
    }
    if (!force && width === this._chartWidth) {
      return;
    }
    this._chartWidth = width;
    let minTemp = 10;
    let maxTemp = 70;
    for (const series of this._chartSeries) {
      for (const point of series.points) {
        minTemp = Math.min(minTemp, point.value);
        maxTemp = Math.max(maxTemp, point.value);
      }
    }
    minTemp = Math.floor(minTemp / 5) * 5;
    maxTemp = Math.ceil(maxTemp / 5) * 5;
    if (maxTemp <= minTemp) {
      maxTemp = minTemp + 10;
    }
    const step = maxTemp - minTemp > 40 ? 10 : 5;
    const height = 240;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.floor(width * dpr);
    canvas.height = Math.floor(height * dpr);
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      return;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.imageSmoothingEnabled = false;
    ctx.clearRect(0, 0, width, height);
    const pad = { top: 18, right: 12, bottom: 28, left: 36 };
    const plotW = width - pad.left - pad.right;
    const plotH = height - pad.top - pad.bottom;
    const end = Date.now();
    const start = end - CHART_MS;
    const xAt = (time) => Math.round(pad.left + ((time - start) / (end - start)) * plotW) + 0.5;
    const yAt = (value) =>
      Math.round(pad.top + plotH - ((value - minTemp) / (maxTemp - minTemp)) * plotH) + 0.5;
    ctx.fillStyle = "#0a1a12";
    ctx.fillRect(pad.left, pad.top, plotW, plotH);
    const clipX = (x) => Math.max(pad.left, Math.min(pad.left + plotW, x));
    for (const band of this._rateBands(start, end)) {
      const x0 = clipX(xAt(band.from));
      const x1 = clipX(xAt(band.to));
      if (x1 <= x0) {
        continue;
      }
      ctx.fillStyle = RATE_COLORS[band.kind];
      ctx.fillRect(x0, pad.top, x1 - x0, plotH);
    }
    ctx.strokeStyle = "#1e3a28";
    ctx.lineWidth = 1;
    ctx.font = "11px sans-serif";
    for (let tick = minTemp; tick <= maxTemp; tick += step) {
      const y = yAt(tick);
      ctx.beginPath();
      ctx.moveTo(pad.left, y);
      ctx.lineTo(pad.left + plotW, y);
      ctx.stroke();
      ctx.fillStyle = "#3a6b48";
      ctx.fillText(`${tick}°`, 2, y + 4);
    }
    ctx.fillStyle = "rgba(125, 255, 154, 0.22)";
    for (const band of this._powerBands(end)) {
      const x0 = clipX(xAt(band.from));
      const x1 = clipX(xAt(band.to));
      if (x1 <= x0) {
        continue;
      }
      ctx.fillRect(x0, pad.top, x1 - x0, plotH);
    }
    for (const line of this._chartSeries) {
      if (!line.points.length) {
        continue;
      }
      ctx.strokeStyle = line.color;
      ctx.lineWidth = 2;
      ctx.beginPath();
      line.points.forEach((point, index) => {
        const x = xAt(point.time);
        const y = yAt(point.value);
        if (index === 0) {
          ctx.moveTo(x, y);
        } else {
          ctx.lineTo(x, y);
        }
      });
      ctx.stroke();
    }
    this._drawTimeLine(ctx, this._hourMarks(start, end, 0), xAt, pad, plotW, plotH, false);
    this._drawTimeLine(ctx, this._hourMarks(start, end, 12), xAt, pad, plotW, plotH, true);
    this._drawModeMarks(ctx, start, end, xAt, pad, plotW, plotH);
    ctx.fillStyle = "#8a97a3";
    for (const midnight of this._hourMarks(start, end, 0)) {
      ctx.fillText(
        new Date(midnight).toLocaleDateString(undefined, { weekday: "short" }),
        clipX(xAt(midnight)) + 4,
        height - 8
      );
    }
    if (legend) {
      const rates = RATE_LEGEND.map(
        (rate) =>
          `<span><span class="swatch" style="background:${rate.color}"></span>${rate.name}</span>`
      ).join("");
      const marks =
        `<span><span class="swatch midnight"></span>Midnight</span>` +
        `<span><span class="swatch noon"></span>Noon</span>` +
        `<span><span class="swatch mode"></span>Mode switch</span>`;
      const items = this._chartSeries
        .map(
          (line) =>
            `<span><span class="swatch" style="background:${line.color}"></span>${line.name}</span>`
        )
        .join("");
      const power = this._powerSeries.length
        ? `<span><span class="swatch" style="background:rgba(125,255,154,0.45)"></span>Heater on</span>`
        : "";
      legend.innerHTML = `${rates}${marks}${items}${power}`;
    }
  }

  _drawModeMarks(ctx, start, end, xAt, pad, plotW, plotH) {
    ctx.save();
    ctx.strokeStyle = "#ffe08a";
    ctx.fillStyle = "#ffe08a";
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 3]);
    ctx.font = "10px sans-serif";
    let lastLabelX = -40;
    for (const mark of this._modeMarks()) {
      if (mark.time < start || mark.time > end) {
        continue;
      }
      const x = xAt(mark.time);
      if (x < pad.left || x > pad.left + plotW) {
        continue;
      }
      ctx.beginPath();
      ctx.moveTo(x, pad.top);
      ctx.lineTo(x, pad.top + plotH);
      ctx.stroke();
      if (x - lastLabelX > 36) {
        ctx.setLineDash([]);
        ctx.fillText(this._modeLabel(mark.mode), x + 3, pad.top + 10);
        ctx.setLineDash([3, 3]);
        lastLabelX = x;
      }
    }
    ctx.restore();
  }

  _localDayStart(ms) {
    const day = new Date(ms);
    day.setHours(0, 0, 0, 0);
    return day.getTime();
  }

  _localAt(dayStart, hour) {
    const stamp = new Date(dayStart);
    stamp.setHours(hour, 0, 0, 0);
    return stamp.getTime();
  }

  _rateBands(start, end) {
    const bands = [];
    let cursor = this._localDayStart(start) - DAY_MS;
    const last = this._localDayStart(end) + DAY_MS;
    while (cursor <= last) {
      const next = cursor + DAY_MS;
      bands.push(
        { kind: "shoulder", from: this._localAt(cursor, 0), to: this._localAt(cursor, 11) },
        { kind: "low", from: this._localAt(cursor, 11), to: this._localAt(cursor, 16) },
        { kind: "peak", from: this._localAt(cursor, 16), to: this._localAt(cursor, 21) },
        { kind: "shoulder", from: this._localAt(cursor, 21), to: next }
      );
      cursor = next;
    }
    return bands
      .map((band) => ({
        kind: band.kind,
        from: Math.max(band.from, start),
        to: Math.min(band.to, end),
      }))
      .filter((band) => band.to > band.from);
  }

  _hourMarks(start, end, hour) {
    const marks = [];
    const cursor = new Date(start);
    cursor.setHours(hour, 0, 0, 0);
    if (cursor.getTime() < start) {
      cursor.setDate(cursor.getDate() + 1);
    }
    while (cursor.getTime() <= end) {
      marks.push(cursor.getTime());
      cursor.setDate(cursor.getDate() + 1);
    }
    return marks;
  }

  _drawTimeLine(ctx, times, xAt, pad, plotW, plotH, dotted) {
    ctx.save();
    ctx.strokeStyle = "#c9d6de";
    ctx.lineWidth = dotted ? 1 : 1.5;
    ctx.setLineDash(dotted ? [4, 4] : []);
    for (const time of times) {
      const x = xAt(time);
      if (x < pad.left || x > pad.left + plotW) {
        continue;
      }
      ctx.beginPath();
      ctx.moveTo(x, pad.top);
      ctx.lineTo(x, pad.top + plotH);
      ctx.stroke();
    }
    ctx.restore();
  }
}

customElements.define("midea-w-heater-panel", MideaWHeaterPanel);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "midea-w-heater-panel",
  name: "Midea W-Heater",
  description: "Chromagen HP170 panel with tank, evaporator, and ambient temperatures",
});
