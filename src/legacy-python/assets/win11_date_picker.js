(function () {
  "use strict";

  const months = Array.from({length: 12}, (_, index) => `${index + 1}월`);
  const weekdays = ["일", "월", "화", "수", "목", "금", "토"];
  let picker = null;
  let activeInput = null;
  let cursor = new Date();
  let view = "day";
  let yearPage = cursor.getFullYear() - 5;

  function parseValue(value) {
    const normalized = String(value || "").replaceAll(".", "-");
    const match = normalized.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    const date = match
      ? new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]))
      : new Date();
    return Number.isNaN(date.getTime()) ? new Date() : date;
  }

  function isoValue(date) {
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
  }

  function displayValue(date) {
    return isoValue(date).replaceAll("-", ".");
  }

  function ensurePicker() {
    if (picker) return picker;
    picker = document.createElement("div");
    picker.className = "win11-date-picker dash-win11-date-picker";
    picker.hidden = true;
    picker.setAttribute("role", "dialog");
    picker.setAttribute("aria-label", "날짜 선택");
    document.body.append(picker);
    picker.addEventListener("click", handlePickerClick);
    return picker;
  }

  function position() {
    if (!activeInput || !picker) return;
    const rect = activeInput.getBoundingClientRect();
    const width = Math.min(320, window.innerWidth - 24);
    picker.style.left = `${Math.max(12, Math.min(rect.left, window.innerWidth - width - 12))}px`;
    picker.style.top = `${Math.min(rect.bottom + 8, window.innerHeight - picker.offsetHeight - 12)}px`;
  }

  function render() {
    ensurePicker();
    const year = cursor.getFullYear();
    const month = cursor.getMonth();
    const selected = activeInput ? isoValue(parseValue(activeInput.value)) : "";
    const today = isoValue(new Date());
    const title = view === "day" ? `${year}년 ${months[month]}` : view === "month" ? `${year}년` : `${yearPage} - ${yearPage + 11}`;
    let body = "";
    if (view === "day") {
      const first = new Date(year, month, 1);
      const start = new Date(year, month, 1 - first.getDay());
      const days = [];
      for (let index = 0; index < 42; index += 1) {
        const date = new Date(start);
        date.setDate(start.getDate() + index);
        const value = isoValue(date);
        const classes = ["win11-date-day"];
        if (date.getMonth() !== month) classes.push("is-outside");
        if (value === today) classes.push("is-today");
        if (value === selected) classes.push("is-selected");
        days.push(`<button type="button" class="${classes.join(" ")}" data-day="${value}">${date.getDate()}</button>`);
      }
      body = `<div class="win11-date-weekdays">${weekdays.map(day => `<span>${day}</span>`).join("")}</div><div class="win11-date-days">${days.join("")}</div>`;
    } else if (view === "month") {
      body = `<div class="win11-date-choices">${months.map((name, index) => `<button type="button" class="win11-date-choice ${index === month ? "is-selected" : ""}" data-month="${index}">${name}</button>`).join("")}</div>`;
    } else {
      body = `<div class="win11-date-choices">${Array.from({length: 12}, (_, index) => yearPage + index).map(yearValue => `<button type="button" class="win11-date-choice ${yearValue === year ? "is-selected" : ""}" data-year="${yearValue}">${yearValue}</button>`).join("")}</div>`;
    }
    picker.innerHTML = `<div class="win11-date-header"><button type="button" class="win11-date-nav" data-nav="prev" aria-label="이전">‹</button><button type="button" class="win11-date-title" data-view="${view === "day" ? "month" : view === "month" ? "year" : "day"}">${title}</button><button type="button" class="win11-date-nav" data-nav="next" aria-label="다음">›</button></div>${body}`;
    picker.classList.toggle("is-dark", document.getElementById("research-page")?.classList.contains("theme-dark"));
    position();
  }

  function close() {
    if (picker) picker.hidden = true;
    activeInput = null;
  }

  function commit(date) {
    if (!activeInput) return;
    const input = activeInput;
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    setter.call(input, displayValue(date));
    input.dispatchEvent(new Event("input", {bubbles: true}));
    input.dispatchEvent(new Event("change", {bubbles: true}));
    input.dispatchEvent(new Event("blur", {bubbles: true}));
    close();
  }

  function handlePickerClick(event) {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.dataset.day) return commit(parseValue(button.dataset.day));
    if (button.dataset.month !== undefined) {
      cursor.setMonth(Number(button.dataset.month));
      view = "day";
    } else if (button.dataset.year) {
      cursor.setFullYear(Number(button.dataset.year));
      view = "month";
    } else if (button.dataset.view) {
      view = button.dataset.view;
    } else if (button.dataset.nav) {
      const amount = button.dataset.nav === "prev" ? -1 : 1;
      if (view === "day") cursor.setMonth(cursor.getMonth() + amount);
      else if (view === "month") cursor.setFullYear(cursor.getFullYear() + amount);
      else yearPage += amount * 12;
    }
    render();
  }

  document.addEventListener("pointerdown", event => {
    const input = event.target.closest(".DateInput_input");
    if (input) {
      event.preventDefault();
      event.stopImmediatePropagation();
      activeInput = input;
      cursor = parseValue(input.value);
      yearPage = cursor.getFullYear() - 5;
      view = "day";
      ensurePicker().hidden = false;
      render();
      return;
    }
    if (picker && !picker.hidden && !picker.contains(event.target)) close();
  }, true);

  window.addEventListener("resize", position);
  window.addEventListener("scroll", position, true);
})();
