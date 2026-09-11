(function () {
  "use strict";

  const months = ["1월", "2월", "3월", "4월", "5월", "6월", "7월", "8월", "9월", "10월", "11월", "12월"];
  const weekdays = ["일", "월", "화", "수", "목", "금", "토"];
  let activePicker = null;

  function fromValue(value) {
    const date = value ? new Date(`${value}T00:00:00`) : new Date();
    return Number.isNaN(date.getTime()) ? new Date() : date;
  }

  function toValue(date) {
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
  }

  function closePicker() {
    if (!activePicker) return;
    activePicker.hidden = true;
    activePicker = null;
  }

  function positionPicker(picker, input) {
    const rect = input.getBoundingClientRect();
    const width = Math.min(320, window.innerWidth - 24);
    const left = Math.max(12, Math.min(rect.left, window.innerWidth - width - 12));
    picker.style.left = `${left}px`;
    picker.style.top = `${Math.max(12, Math.min(rect.bottom + 8, window.innerHeight - picker.offsetHeight - 12))}px`;
  }

  function createPicker(input) {
    const picker = document.createElement("div");
    picker.className = "win11-date-picker";
    picker.hidden = true;
    picker.setAttribute("role", "dialog");
    picker.setAttribute("aria-label", "날짜 선택");
    document.body.append(picker);
    let cursor = fromValue(input.value);
    let view = "day";
    let yearPage = cursor.getFullYear() - 5;

    function selectDate(date) {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
      setter.call(input, toValue(date));
      /* React/Dash와 일반 HTML 모두 값 변경을 인식하도록 두 이벤트를 보낸다. */
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      closePicker();
    }

    function render() {
      const year = cursor.getFullYear();
      const month = cursor.getMonth();
      const selected = input.value;
      const today = toValue(new Date());
      const title = view === "day" ? `${year}년 ${months[month]}` : view === "month" ? `${year}년` : `${yearPage} - ${yearPage + 11}`;
      if (view === "day") {
        const first = new Date(year, month, 1);
        const start = new Date(year, month, 1 - first.getDay());
        const choices = [];
        for (let index = 0; index < 42; index += 1) {
          const date = new Date(start);
          date.setDate(start.getDate() + index);
          const value = toValue(date);
          const classes = ["win11-date-day"];
          if (date.getMonth() !== month) classes.push("is-outside");
          if (value === today) classes.push("is-today");
          if (value === selected) classes.push("is-selected");
          choices.push(`<button type="button" class="${classes.join(" ")}" data-day="${value}" aria-label="${value}">${date.getDate()}</button>`);
        }
        picker.innerHTML = `<div class="win11-date-header"><button type="button" class="win11-date-nav" data-nav="prev" aria-label="이전 달">‹</button><button type="button" class="win11-date-title" data-view="month">${title}</button><button type="button" class="win11-date-nav" data-nav="next" aria-label="다음 달">›</button></div><div class="win11-date-weekdays">${weekdays.map(day => `<span>${day}</span>`).join("")}</div><div class="win11-date-days">${choices.join("")}</div>`;
      } else if (view === "month") {
        picker.innerHTML = `<div class="win11-date-header"><button type="button" class="win11-date-nav" data-nav="prev" aria-label="이전 해">‹</button><button type="button" class="win11-date-title" data-view="year">${title}</button><button type="button" class="win11-date-nav" data-nav="next" aria-label="다음 해">›</button></div><div class="win11-date-choices">${months.map((name, index) => `<button type="button" class="win11-date-choice ${index === month ? "is-selected" : ""}" data-month="${index}">${name}</button>`).join("")}</div>`;
      } else {
        picker.innerHTML = `<div class="win11-date-header"><button type="button" class="win11-date-nav" data-nav="prev" aria-label="이전 12년">‹</button><button type="button" class="win11-date-title" data-view="day">${title}</button><button type="button" class="win11-date-nav" data-nav="next" aria-label="다음 12년">›</button></div><div class="win11-date-choices">${Array.from({ length: 12 }, (_, index) => { const item = yearPage + index; return `<button type="button" class="win11-date-choice ${item === year ? "is-selected" : ""}" data-year="${item}">${item}</button>`; }).join("")}</div>`;
      }
      picker.classList.toggle("is-dark", document.documentElement.dataset.theme === "dark" || document.body.classList.contains("research-theme-dark"));
      positionPicker(picker, input);
    }

    picker.addEventListener("click", event => {
      const button = event.target.closest("button");
      if (!button) return;
      if (button.dataset.day) return selectDate(fromValue(button.dataset.day));
      if (button.dataset.month !== undefined) { cursor.setMonth(Number(button.dataset.month)); view = "day"; render(); return; }
      if (button.dataset.year) { cursor.setFullYear(Number(button.dataset.year)); view = "month"; render(); return; }
      if (button.dataset.view) { view = button.dataset.view; render(); return; }
      if (button.dataset.nav) {
        const amount = button.dataset.nav === "prev" ? -1 : 1;
        if (view === "day") cursor.setMonth(cursor.getMonth() + amount);
        else if (view === "month") cursor.setFullYear(cursor.getFullYear() + amount);
        else yearPage += amount * 12;
        render();
      }
    });
    input.addEventListener("click", () => {
      const opening = picker.hidden;
      closePicker();
      if (!opening) return;
      cursor = fromValue(input.value);
      yearPage = cursor.getFullYear() - 5;
      view = "day";
      picker.hidden = false;
      render();
      activePicker = picker;
    });
    input.addEventListener("keydown", event => {
      if (event.key === "Escape") closePicker();
      if ((event.key === "ArrowDown" || event.key === "Enter") && picker.hidden) { event.preventDefault(); input.click(); }
    });
    picker._input = input;
  }

  function mountDateInput(input) {
    if (!input || input.dataset.researchDatePickerBound) return;
    input.dataset.researchDatePickerBound = "1";
    /* 기존 HTML 초기화 코드도 같은 입력에 두 번째 달력을 붙이지 않게 한다. */
    input.dataset.win11DatePickerBound = "1";
    input.type = "text";
    input.readOnly = true;
    input.classList.add("research-date-input");
    input.parentElement?.classList.add("research-date-range");
    input.setAttribute("aria-haspopup", "dialog");
    createPicker(input);
  }

  function mountAll(root) {
    const scope = root && root.querySelectorAll ? root : document;
    scope.querySelectorAll(".research-date-input, #start-date, #end-date, #indicator-start-date, #indicator-end-date, #research-start-date, #research-end-date, #research-indicator-start-date, #research-indicator-end-date").forEach(mountDateInput);
    if (scope.matches?.(".research-date-input")) mountDateInput(scope);
  }

  document.addEventListener("pointerdown", event => {
    if (activePicker && !activePicker.contains(event.target) && event.target !== activePicker._input) closePicker();
  });
  mountAll(document);
  document.addEventListener("DOMContentLoaded", () => mountAll(document));
  new MutationObserver(records => records.forEach(record => record.addedNodes.forEach(mountAll))).observe(document.documentElement, { childList: true, subtree: true });
  window.ResearchSharedUi = { mountAll, mountDateInput };
}());
