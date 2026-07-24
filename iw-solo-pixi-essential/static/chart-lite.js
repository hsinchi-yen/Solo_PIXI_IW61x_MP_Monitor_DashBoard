class Chart {
  constructor(canvas, config) {
    this.canvas = canvas;
    this.config = config;
    this.draw();
  }

  destroy() {
    const ctx = this.canvas.getContext("2d");
    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
  }

  draw() {
    const ctx = this.canvas.getContext("2d");
    const labels = this.config.data.labels || [];
    const datasets = this.config.data.datasets || [];
    const values = datasets[0]?.data || [];
    const color = datasets[0]?.backgroundColor || datasets[0]?.borderColor || "#50c8b8";
    const width = this.canvas.clientWidth || 640;
    const height = Number(this.canvas.getAttribute("height")) || 180;
    this.canvas.width = width * devicePixelRatio;
    this.canvas.height = height * devicePixelRatio;
    ctx.scale(devicePixelRatio, devicePixelRatio);
    ctx.clearRect(0, 0, width, height);
    ctx.font = "12px Segoe UI";
    ctx.fillStyle = getComputedStyle(document.body).getPropertyValue("--muted").trim() || "#9ba7b8";
    if (!values.length) {
      ctx.fillText("No data", 16, 28);
      return;
    }
    if (this.config.type === "doughnut") {
      this.drawDoughnut(ctx, labels, values, width, height);
      return;
    }
    const max = Math.max(...values, 1);
    const pad = 28;
    const slot = (width - pad * 2) / values.length;
    ctx.strokeStyle = "rgba(155,167,184,.28)";
    ctx.beginPath();
    ctx.moveTo(pad, height - pad);
    ctx.lineTo(width - pad, height - pad);
    ctx.stroke();
    if (this.config.type === "line") {
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.beginPath();
      values.forEach((value, index) => {
        const x = pad + slot * index + slot / 2;
        const y = height - pad - (value / max) * (height - pad * 2);
        index ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      });
      ctx.stroke();
    } else {
      ctx.fillStyle = color;
      values.forEach((value, index) => {
        const barHeight = (value / max) * (height - pad * 2);
        ctx.fillRect(pad + slot * index + 4, height - pad - barHeight, Math.max(5, slot - 8), barHeight);
      });
    }
    const stride = Math.max(1, Math.ceil(labels.length / 8));
    labels.forEach((label, index) => {
      if (index % stride !== 0) return;
      ctx.fillText(String(label).slice(0, 12), pad + slot * index + 4, height - 8);
    });
  }

  drawDoughnut(ctx, labels, values, width, height) {
    const colors = this.config.data.datasets[0]?.backgroundColor || ["#50c8b8", "#ff6b6b", "#f7c948"];
    const total = values.reduce((sum, value) => sum + value, 0) || 1;
    const radius = Math.min(width, height) / 3;
    let start = -Math.PI / 2;
    values.forEach((value, index) => {
      const angle = (value / total) * Math.PI * 2;
      ctx.beginPath();
      ctx.moveTo(width / 2, height / 2);
      ctx.arc(width / 2, height / 2, radius, start, start + angle);
      ctx.closePath();
      ctx.fillStyle = colors[index % colors.length];
      ctx.fill();
      start += angle;
    });
    ctx.fillStyle = getComputedStyle(document.body).getPropertyValue("--panel").trim() || "#181c22";
    ctx.beginPath();
    ctx.arc(width / 2, height / 2, radius * 0.55, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = getComputedStyle(document.body).getPropertyValue("--muted").trim() || "#9ba7b8";
    labels.forEach((label, index) => ctx.fillText(`${label}: ${values[index]}`, 16, 20 + index * 18));
  }
}

window.Chart = Chart;
