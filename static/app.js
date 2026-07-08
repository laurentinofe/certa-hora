const $ = (selector) => document.querySelector(selector);
const h = (value) => String(value ?? "").replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[character]));
const labels = { ENTRADA: "Entrada", SAIDA_ALMOCO: "Saída para almoço", RETORNO_ALMOCO: "Retorno do almoço", SAIDA: "Saída" };
const states = { SEM_REGISTROS: "Sem registros", INCOMPLETO: "Jornada incompleta", HORA_EXTRA: "Hora extra", CARGA_INFERIOR: "Carga inferior", REGULAR: "Regular" };
const overtimeStatus = { PENDING: "Aguardando análise", APPROVED: "Justificativa validada", REJECTED: "Justificativa contestada" };
const overtimeTreatment = { PAYMENT: "Pagamento", TIME_BANK: "Banco de horas", HR_REVIEW: "Encaminhado ao RH" };
let currentUser = null;
let employeeData = null;
let employeeHistory = [];
let managerUsers = [];

async function api(url, options = {}) {
  const response = await fetch(url, { headers: { "Content-Type": "application/json" }, ...options });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ error: "Falha na comunicação." }));
    const error = new Error(body.error || "Não foi possível concluir.");
    error.data = body;
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function show(view) {
  ["#loginView", "#employeeView", "#managerView"].forEach(id => $(id).classList.add("hidden"));
  $(view).classList.remove("hidden");
}

function toast(message, danger = false) {
  const element = $("#toast");
  element.textContent = message;
  element.className = `toast ${danger ? "danger" : ""}`;
  setTimeout(() => element.classList.add("hidden"), 3500);
}

function minutes(value) {
  return `${Math.floor(value / 60)}h ${String(value % 60).padStart(2, "0")}min`;
}

function localDate(value) {
  return new Date(`${value}T12:00:00`).toLocaleDateString("pt-BR");
}

function captureLocation() {
  return new Promise(resolve => {
    if (!navigator.geolocation) {
      return resolve({ status: "UNAVAILABLE" });
    }
    navigator.geolocation.getCurrentPosition(
      position => resolve({
        status: "CAPTURED",
        latitude: position.coords.latitude,
        longitude: position.coords.longitude,
        accuracy: position.coords.accuracy
      }),
      error => resolve({
        status: error.code === error.PERMISSION_DENIED
          ? "DENIED"
          : error.code === error.TIMEOUT ? "TIMEOUT" : "UNAVAILABLE"
      }),
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 }
    );
  });
}

function locationSummary(locations = []) {
  const captured = locations.find(location => location.status === "CAPTURED");
  const geofence = locations.find(location => location.geofence_status && !["NOT_EVALUATED", "DISABLED", "INACTIVE_FOR_PUNCH"].includes(location.geofence_status));
  const geofenceText = geofence ? `<small class="geofence-${geofence.geofence_status}">${geofence.geofence_status === "INSIDE" ? "Dentro da cerca" : geofence.geofence_status === "OUTSIDE" ? `Fora da cerca: ${Math.round(geofence.geofence_distance_meters || 0)} m de ${h(geofence.geofence_reference || "local")}${geofence.geofence_reason ? ` · ${h(geofence.geofence_reason)}` : ""}` : "Cerca não validada"}</small>` : "";
  if (captured) {
    const url = `https://www.google.com/maps?q=${captured.latitude},${captured.longitude}`;
    return `<a href="${url}" target="_blank" rel="noopener">Ver localização</a><small>Precisão aproximada: ${Math.round(captured.accuracy)} m</small>`;
  }
  const status = locations[0]?.status;
  return `<small>${status === "DENIED" ? "Localização não autorizada" : status === "TIMEOUT" ? "Localização não obtida a tempo" : "Localização indisponível"}</small>`;
}

function geofenceSummary(locations = []) {
  const geofence = locations.find(location => location.geofence_status && !["NOT_EVALUATED", "DISABLED", "INACTIVE_FOR_PUNCH"].includes(location.geofence_status));
  if (!geofence) return "";
  if (geofence.geofence_status === "INSIDE") return `<small class="geofence-INSIDE">Dentro da cerca</small>`;
  if (geofence.geofence_status === "OUTSIDE") return `<small class="geofence-OUTSIDE">Fora da cerca: ${Math.round(geofence.geofence_distance_meters || 0)} m de ${h(geofence.geofence_reference || "local")}${geofence.geofence_reason ? ` Â· ${h(geofence.geofence_reason)}` : ""}</small>`;
  return `<small class="geofence-${geofence.geofence_status}">Cerca nÃ£o validada</small>`;
}

async function boot() {
  const data = await api("/api/me");
  if (!data.user) return show("#loginView");
  currentUser = data.user;
  if (currentUser.role === "MANAGER") await openManager();
  else await openEmployee();
}

$("#loginForm").addEventListener("submit", async event => {
  event.preventDefault();
  try {
    const data = await api("/api/login", { method: "POST", body: JSON.stringify({ registration: $("#registration").value, password: $("#password").value }) });
    currentUser = data.user;
    if (currentUser.role === "MANAGER") await openManager();
    else await openEmployee();
  } catch (error) { toast(error.message, true); }
});

document.querySelectorAll(".logout").forEach(button => button.addEventListener("click", async () => {
  await api("/api/logout", { method: "POST", body: "{}" });
  currentUser = null;
  show("#loginView");
}));

$("#changePasswordButton").addEventListener("click", () => confirmModal("Alterar minha senha", `<div class="form-stack"><label>Senha atual<input id="currentPassword" type="password" autocomplete="current-password"></label><label>Nova senha<input id="newPassword" type="password" minlength="8" autocomplete="new-password"></label><label>Confirmar nova senha<input id="confirmNewPassword" type="password" minlength="8" autocomplete="new-password"></label><p class="legal-note">Após a alteração, todas as sessões desta matrícula serão encerradas.</p></div>`, async () => {
  const newPassword = $("#newPassword").value;
  if (newPassword !== $("#confirmNewPassword").value) throw new Error("A confirmação da nova senha não confere.");
  const result = await api("/api/change-password", {
    method: "POST",
    body: JSON.stringify({ current_password: $("#currentPassword").value, new_password: newPassword })
  });
  $("#modal").classList.add("hidden");
  currentUser = null;
  show("#loginView");
  $("#password").value = "";
  toast(result.message);
}, true));

async function openEmployee() {
  show("#employeeView");
  $("#employeeName").textContent = currentUser.name;
  const hour = new Date().getHours();
  $("#greeting").textContent = `${hour < 12 ? "Bom dia" : hour < 18 ? "Boa tarde" : "Boa noite"}, ${currentUser.name.split(" ")[0]}`;
  $("#correctionDate").value = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  $("#historyMonth").value = new Date().toISOString().slice(0, 7);
  await loadEmployee();
  await loadEmployeeHistory();
}

async function loadEmployee() {
  employeeData = await api("/api/dashboard");
  const settings = employeeData.settings;
  $("#employeeSchedule").textContent = `${settings.work_start}–${settings.lunch_start} · ${settings.lunch_end}–${settings.work_end}`;
  $("#todayLabel").textContent = new Date(employeeData.server_time).toLocaleDateString("pt-BR", { weekday: "long", day: "2-digit", month: "long" });
  $("#nextPunch").textContent = labels[employeeData.next_type];
  $("#workedTime").textContent = minutes(employeeData.summary.worked_minutes);
  $("#dayState").textContent = states[employeeData.summary.state];
  $("#dayState").className = `status state-${employeeData.summary.state}`;
  $("#timeline").innerHTML = employeeData.punches.length ? employeeData.punches.map(p => `<div class="timeline-item"><span class="dot"></span><div><strong>${labels[p.punch_type]}</strong><small>${new Date(p.punched_at).toLocaleTimeString("pt-BR")}${p.corrected ? " · corrigido" : ""}</small></div></div>`).join("") : `<p class="empty">Nenhuma marcação realizada hoje.</p>`;
  $("#correctionList").innerHTML = employeeData.corrections.slice(0, 5).map(c => `<div class="compact-item"><span>${c.requested_at_value ? new Date(c.requested_at_value).toLocaleString("pt-BR") : c.action}</span><b class="status state-${c.status}">${c.status}</b></div>`).join("");
  const needsReason = employeeData.summary.overtime_minutes > 0 && !employeeData.justification;
  $("#overtimeCard").classList.toggle("hidden", !needsReason);
  $("#overtimeValue").textContent = `${minutes(employeeData.summary.overtime_minutes)} além da jornada`;
  const reviewed = employeeData.summary.overtime_minutes > 0 && employeeData.justification;
  $("#overtimeReviewCard").classList.toggle("hidden", !reviewed);
  if (reviewed) {
    const item = employeeData.justification;
    $("#overtimeReviewTitle").textContent = overtimeStatus[item.status] || "Justificativa enviada";
    $("#overtimeReviewDetails").innerHTML = `<p><strong>Horas apuradas:</strong> ${minutes(employeeData.summary.overtime_minutes)} — preservadas independentemente da análise.</p><p><strong>Motivo informado:</strong> ${h(item.reason)}</p>${item.treatment ? `<p><strong>Tratamento:</strong> ${overtimeTreatment[item.treatment] || h(item.treatment)}</p>` : ""}${item.review_note ? `<p><strong>Observação do gestor:</strong> ${h(item.review_note)}</p>` : ""}`;
  }
}

async function loadEmployeeHistory() {
  try {
    const data = await api(`/api/employee/history?month=${encodeURIComponent($("#historyMonth").value)}`);
    employeeHistory = data.history;
    const pending = data.pending_overtime.length;
    $("#pendingOvertimeAlert").classList.toggle("hidden", pending === 0);
    $("#pendingOvertimeAlert").innerHTML = pending ? `<strong>${pending} hora(s) extra(s) aguardando justificativa.</strong> Use o botão “Justificar” na data correspondente.` : "";
    $("#employeeHistoryBody").innerHTML = employeeHistory.length ? employeeHistory.map(r => `<tr>
      <td>${localDate(r.date)}</td>
      <td>${r.times.length ? r.times.join(" · ") : "—"}${r.times.length ? locationSummary(r.locations) + geofenceSummary(r.locations) : ""}</td>
      <td>${minutes(r.worked_minutes)}</td>
      <td><span class="status state-${r.state}">${states[r.state]}</span></td>
      <td>${r.overtime_minutes ? `<strong>+${minutes(r.overtime_minutes)}</strong>${r.overtime_reason ? `<small>${overtimeStatus[r.overtime_status] || "Justificativa enviada"}${r.overtime_treatment ? ` · ${overtimeTreatment[r.overtime_treatment]}` : ""}</small>` : `<button class="text-button" onclick="justifyHistoricalOvertime('${r.date}')">Justificar</button>`}` : "—"}</td>
      <td><small>${h(r.day_note || "")}</small></td>
    </tr>`).join("") : `<tr><td colspan="6" class="empty">Nenhum registro disponível neste mês.</td></tr>`;
  } catch (error) {
    toast(error.message, true);
  }
}

$("#historyMonth").addEventListener("change", loadEmployeeHistory);

window.justifyHistoricalOvertime = workDate => confirmModal("Justificar hora extra", `<div class="form-stack"><p>Data: <strong>${localDate(workDate)}</strong></p><label>Motivo<textarea id="historicalOvertimeReason" minlength="5" required placeholder="Descreva o motivo da hora extra"></textarea></label></div>`, async () => {
  await api("/api/overtime-justification", { method: "POST", body: JSON.stringify({ work_date: workDate, reason: $("#historicalOvertimeReason").value }) });
  toast("Justificativa enviada ao gestor.");
  await loadEmployee();
  await loadEmployeeHistory();
}, true);

setInterval(() => { $("#clock").textContent = new Date().toLocaleTimeString("pt-BR"); }, 500);

async function sendPunch(location, confirmClose = false, geofenceReason = "") {
  return api("/api/punch", {
    method: "POST",
    body: JSON.stringify({ location, confirm_close: confirmClose, geofence_reason: geofenceReason })
  });
}

$("#punchButton").addEventListener("click", async () => {
  try {
    const location = await captureLocation();
    await sendPunch(location);
    toast("Ponto registrado com sucesso.");
    await loadEmployee();
    await loadEmployeeHistory();
  } catch (error) {
    if (error.data?.confirmation_required) {
      confirmModal("Confirmar nova marcação", error.message, async () => {
        const location = await captureLocation();
        await sendPunch(location, true);
        toast("Nova marcação confirmada.");
        await loadEmployee();
        await loadEmployeeHistory();
      });
    } else if (error.data?.geofence_reason_required) {
      confirmModal("Ponto fora da cerca", `<div class="form-stack"><p>VocÃª estÃ¡ a aproximadamente <strong>${error.data.distance_meters} m</strong> de <strong>${h(error.data.reference)}</strong>. O ponto serÃ¡ registrado, mas precisa de justificativa.</p><label>Justificativa<textarea id="geofenceReason" minlength="5" required placeholder="Ex.: atendimento externo ao cliente"></textarea></label></div>`, async () => {
        const location = await captureLocation();
        await sendPunch(location, false, $("#geofenceReason").value);
        toast("Ponto registrado com justificativa de localizaÃ§Ã£o.");
        await loadEmployee();
        await loadEmployeeHistory();
      }, true);
    } else toast(error.message, true);
  }
});

$("#overtimeForm").addEventListener("submit", async event => {
  event.preventDefault();
  try {
    await api("/api/overtime-justification", { method: "POST", body: JSON.stringify({ work_date: employeeData.date, reason: $("#overtimeReason").value }) });
    toast("Justificativa enviada.");
    await loadEmployee();
    await loadEmployeeHistory();
  } catch (error) { toast(error.message, true); }
});

$("#correctionForm").addEventListener("submit", async event => {
  event.preventDefault();
  try {
    await api("/api/corrections", { method: "POST", body: JSON.stringify({ action: "ADD", requested_at_value: new Date($("#correctionDate").value).toISOString(), requested_type: $("#correctionType").value, reason: $("#correctionReason").value }) });
    event.target.reset();
    toast("Solicitação enviada ao gestor.");
    await loadEmployee();
  } catch (error) { toast(error.message, true); }
});

async function openManager() {
  show("#managerView");
  $("#managerName").textContent = currentUser.name;
  await loadSettings();
  await loadManagerUsers();
  const today = new Date();
  $("#newEmployeeAdmission").value = today.toISOString().slice(0, 10);
  $("#filterTo").value = today.toISOString().slice(0, 10);
  $("#demoDate").value = today.toISOString().slice(0, 10);
  $("#auditTo").value = today.toISOString().slice(0, 10);
  today.setDate(today.getDate() - 30);
  $("#filterFrom").value = today.toISOString().slice(0, 10);
  $("#auditFrom").value = today.toISOString().slice(0, 10);
  await loadManager();
  await loadAudit();
}

async function loadSettings() {
  const data = await api("/api/manager/settings");
  const settings = data.settings;
  $("#companyName").value = settings.company_name;
  $("#companyDocument").value = settings.company_document;
  $("#settingWorkStart").value = settings.work_start;
  $("#settingLunchStart").value = settings.lunch_start;
  $("#settingLunchEnd").value = settings.lunch_end;
  $("#settingWorkEnd").value = settings.work_end;
  $("#settingTolerance").value = settings.tolerance_minutes;
  $("#settingGeofenceEnabled").value = settings.geofence_enabled ? "1" : "0";
  $("#settingGeofenceLabel").value = settings.geofence_label || "Local principal";
  $("#settingGeofenceLatitude").value = settings.geofence_latitude ?? "";
  $("#settingGeofenceLongitude").value = settings.geofence_longitude ?? "";
  $("#settingGeofenceRadius").value = settings.geofence_radius_meters || 200;
}

$("#settingsForm").addEventListener("submit", async event => {
  event.preventDefault();
  try {
    const data = await api("/api/manager/settings", {
      method: "POST",
      body: JSON.stringify({
        company_name: $("#companyName").value,
        company_document: $("#companyDocument").value,
        work_start: $("#settingWorkStart").value,
        lunch_start: $("#settingLunchStart").value,
        lunch_end: $("#settingLunchEnd").value,
        work_end: $("#settingWorkEnd").value,
        tolerance_minutes: Number($("#settingTolerance").value),
        geofence_enabled: $("#settingGeofenceEnabled").value === "1",
        geofence_label: $("#settingGeofenceLabel").value,
        geofence_latitude: $("#settingGeofenceLatitude").value,
        geofence_longitude: $("#settingGeofenceLongitude").value,
        geofence_radius_meters: Number($("#settingGeofenceRadius").value)
      })
    });
    toast(`${data.message} Carga diária: ${minutes(data.workday_minutes)}.`);
    await loadManager();
    await loadAudit();
  } catch (error) { toast(error.message, true); }
});

async function loadManagerUsers(selected = "") {
  const userData = await api("/api/manager/users");
  managerUsers = userData.users;
  $("#filterUser").innerHTML = `<option value="">Todos</option>` + managerUsers.map(u => `<option value="${u.id}">${h(u.name)} · ${h(u.registration)}</option>`).join("");
  $("#demoUser").innerHTML = `<option value="">Selecione</option>` + managerUsers.filter(u => u.active).map(u => `<option value="${u.id}">${h(u.name)} · ${h(u.registration)}</option>`).join("");
  $("#employeeBody").innerHTML = managerUsers.map(u => `<tr>
    <td><strong>${h(u.name)}</strong></td>
    <td>${h(u.registration)}</td>
    <td>${u.admission_date ? localDate(u.admission_date) : "—"}<small>${h(u.position || "")}</small></td>
    <td><span class="status state-${u.active ? "APPROVED" : "REJECTED"}">${u.active ? "Ativo" : "Inativo"}</span></td>
    <td>
      <button class="text-button" onclick="editEmployee(${u.id})">Editar</button>
      <button class="text-button" onclick="resetEmployeePassword(${u.id})">Redefinir senha</button>
      <button class="text-button ${u.active ? "danger-text" : ""}" onclick="toggleEmployee(${u.id})">${u.active ? "Desativar" : "Ativar"}</button>
    </td>
  </tr>`).join("");
  $("#auditActor").innerHTML = `<option value="">Todos</option><option value="${currentUser.id}">${h(currentUser.name)}</option>` + managerUsers.map(u => `<option value="${u.id}">${h(u.name)}</option>`).join("");
  $("#filterUser").value = selected;
}

$("#employeeForm").addEventListener("submit", async event => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  try {
    const data = await api("/api/manager/users", {
      method: "POST",
      body: JSON.stringify({
        name: $("#newEmployeeName").value,
        registration: $("#newEmployeeRegistration").value,
        admission_date: $("#newEmployeeAdmission").value,
        position: $("#newEmployeePosition").value,
        password: $("#newEmployeePassword").value
      })
    });
    event.target.reset();
    await loadManagerUsers(String(data.user.id));
    await loadManager();
    toast("Funcionário cadastrado. A matrícula já pode ser utilizada.");
  } catch (error) {
    toast(error.status === 409 ? "Essa matrícula já está cadastrada." : error.message, true);
  } finally {
    button.disabled = false;
  }
});

window.editEmployee = id => {
  const employee = managerUsers.find(user => user.id === id);
  if (!employee) return;
  confirmModal("Editar funcionário", `<div class="form-stack"><label>Nome completo<input id="editEmployeeName" value="${h(employee.name)}"></label><label>Matrícula<input id="editEmployeeRegistration" value="${h(employee.registration)}"></label><label>Data de admissão<input id="editEmployeeAdmission" type="date" value="${h(employee.admission_date || "")}"></label><label>Cargo<input id="editEmployeePosition" value="${h(employee.position || "")}"></label></div>`, async () => {
    await api(`/api/manager/users/${id}/update`, {
      method: "POST",
      body: JSON.stringify({ name: $("#editEmployeeName").value, registration: $("#editEmployeeRegistration").value, admission_date: $("#editEmployeeAdmission").value, position: $("#editEmployeePosition").value })
    });
    await loadManagerUsers(String(id));
    await loadManager();
    toast("Dados do funcionário atualizados.");
  }, true);
};

window.resetEmployeePassword = id => {
  const employee = managerUsers.find(user => user.id === id);
  if (!employee) return;
  confirmModal("Redefinir senha", `<div class="form-stack"><p>A nova senha substituirá imediatamente o acesso de <strong>${h(employee.name)}</strong>.</p><label>Nova senha<input id="resetPassword" type="password" minlength="8" placeholder="Mínimo de 8 caracteres"></label></div>`, async () => {
    await api(`/api/manager/users/${id}/reset-password`, {
      method: "POST",
      body: JSON.stringify({ password: $("#resetPassword").value })
    });
    toast("Senha redefinida com sucesso.");
  }, true);
};

window.toggleEmployee = id => {
  const employee = managerUsers.find(user => user.id === id);
  if (!employee) return;
  const activate = !employee.active;
  confirmModal(`${activate ? "Ativar" : "Desativar"} funcionário`, `${activate ? "O acesso será restaurado." : "O funcionário não conseguirá entrar até ser reativado."}`, async () => {
    await api(`/api/manager/users/${id}/status`, {
      method: "POST",
      body: JSON.stringify({ active: activate })
    });
    await loadManagerUsers();
    await loadManager();
    toast(activate ? "Funcionário ativado." : "Funcionário desativado.");
  });
};

$("#demoForm").addEventListener("submit", async event => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  try {
    const data = await api("/api/manager/demo-day", {
      method: "POST",
      body: JSON.stringify({
        user_id: $("#demoUser").value,
        work_date: $("#demoDate").value,
        scenario: $("#demoScenario").value
      })
    });
    $("#filterUser").value = $("#demoUser").value;
    $("#filterFrom").value = $("#demoDate").value;
    $("#filterTo").value = $("#demoDate").value;
    await loadManager();
    toast(`${data.message} Resultado: ${states[data.summary.state]}.`);
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
});

function managerQuery() {
  return new URLSearchParams({ from: $("#filterFrom").value, to: $("#filterTo").value, user_id: $("#filterUser").value });
}

async function loadManager() {
  try {
    const data = await api(`/api/manager/report?${managerQuery()}`);
    const counts = data.report.reduce((a, r) => (a[r.state] = (a[r.state] || 0) + 1, a), {});
    $("#managerStats").innerHTML = [
      ["Dias analisados", data.report.length],
      ["Pendências", (counts.SEM_REGISTROS || 0) + (counts.INCOMPLETO || 0)],
      ["Com hora extra", counts.HORA_EXTRA || 0],
      ["Regulares", counts.REGULAR || 0]
    ].map(([name, value]) => `<div class="stat"><span>${name}</span><strong>${value}</strong></div>`).join("");
    $("#reportBody").innerHTML = data.report.map(r => `<tr>
      <td><strong>${r.name}</strong><small>${r.registration}</small></td><td>${localDate(r.date)}</td>
      <td>${r.times.length ? r.times.join(" · ") : "—"}${r.times.length ? locationSummary(r.locations) + geofenceSummary(r.locations) : ""}</td><td>${minutes(r.worked_minutes)}</td>
      <td><span class="status state-${r.state}">${states[r.state]}</span>${r.overtime_minutes ? `<small>+${minutes(r.overtime_minutes)}</small>` : ""}</td>
      <td><small>${h(r.overtime_reason || "")}${r.overtime_status ? `<br><b>${overtimeStatus[r.overtime_status] || ""}</b>` : ""}${r.overtime_treatment ? ` · ${overtimeTreatment[r.overtime_treatment] || ""}` : ""}${r.overtime_review_note ? `<br>${h(r.overtime_review_note)}` : ""}${r.day_note ? `<br>${h(r.day_note)}` : ""}</small></td>
      <td>${r.overtime_minutes ? (r.overtime_reason ? `<button class="text-button" onclick="reviewOvertime(${r.user_id},'${r.date}')">Analisar justificativa</button>` : `<small>Aguardando justificativa</small>`) : ""}<button class="text-button" onclick="dayNote(${r.user_id},'${r.date}')">Ocorrência</button><button class="text-button" onclick="managerCorrection(${r.user_id},'${r.date}')">Corrigir</button></td>
    </tr>`).join("");
    $("#correctionsBody").innerHTML = data.corrections.map(c => `<tr><td>${c.name}<small>${c.registration}</small></td><td>${c.action} · ${c.requested_at_value ? new Date(c.requested_at_value).toLocaleString("pt-BR") : ""}</td><td>${c.reason}</td><td><span class="status state-${c.status}">${c.status}</span></td><td>${c.status === "PENDING" ? `<button class="text-button" onclick="reviewCorrection(${c.id},'APPROVED')">Aprovar</button><button class="text-button danger-text" onclick="reviewCorrection(${c.id},'REJECTED')">Rejeitar</button>` : "—"}</td></tr>`).join("");
  } catch (error) { toast(error.message, true); }
}

async function loadAudit() {
  try {
    const query = new URLSearchParams({
      action: $("#auditAction").value,
      actor_id: $("#auditActor").value,
      from: $("#auditFrom").value,
      to: $("#auditTo").value
    });
    const data = await api(`/api/manager/audit?${query}`);
    const selectedAction = $("#auditAction").value;
    $("#auditAction").innerHTML = `<option value="">Todas</option>` + data.actions.map(action => `<option value="${h(action)}">${h(action)}</option>`).join("");
    $("#auditAction").value = selectedAction;
    $("#auditBody").innerHTML = data.entries.length ? data.entries.map(entry => `<tr><td>${new Date(entry.created_at).toLocaleString("pt-BR")}</td><td>${h(entry.actor_name || "Sistema")}<small>${h(entry.actor_registration || "")}</small></td><td><span class="status">${h(entry.action)}</span></td><td>${h(entry.entity)}${entry.entity_id ? ` #${entry.entity_id}` : ""}</td><td><small class="audit-details">${h(entry.details || "")}</small></td></tr>`).join("") : `<tr><td colspan="5" class="empty">Nenhum evento encontrado.</td></tr>`;
  } catch (error) { toast(error.message, true); }
}

$("#auditFilterButton").addEventListener("click", loadAudit);
$("#backupButton").addEventListener("click", () => { window.location.href = "/api/manager/backup"; });
$("#restoreButton").addEventListener("click", () => {
  const file = $("#restoreFile").files[0];
  if (!file) return toast("Selecione um arquivo de backup.", true);
  confirmModal("Restaurar banco de dados", "Os dados atuais serão substituídos pelos dados do backup e todas as sessões serão encerradas.", async () => {
    if (file.size > 50 * 1024 * 1024) throw new Error("O backup excede o limite de 50 MB.");
    const bytes = new Uint8Array(await file.arrayBuffer());
    let binary = "";
    for (let index = 0; index < bytes.length; index += 32768) {
      binary += String.fromCharCode(...bytes.subarray(index, index + 32768));
    }
    const result = await api("/api/manager/restore", {
      method: "POST",
      body: JSON.stringify({ backup_base64: btoa(binary) })
    });
    currentUser = null;
    show("#loginView");
    toast(result.message);
  });
});

$("#filterButton").addEventListener("click", loadManager);
$("#exportButton").addEventListener("click", () => { window.location.href = `/api/manager/export?${managerQuery()}`; });

window.reviewCorrection = (id, status) => confirmModal(status === "APPROVED" ? "Aprovar correção" : "Rejeitar correção", `<label>Observação<textarea id="reviewNote" placeholder="Opcional"></textarea></label>`, async () => {
  await api(`/api/manager/corrections/${id}`, { method: "POST", body: JSON.stringify({ status, note: $("#reviewNote").value }) });
  toast("Solicitação analisada."); await loadManager();
}, true);

window.dayNote = (userId, workDate) => confirmModal("Registrar ocorrência", `<div class="form-stack"><label>Categoria<select id="noteCategory"><option>FERIADO</option><option>FOLGA</option><option>FALTA_JUSTIFICADA</option><option>ATESTADO</option><option>OUTRO</option></select></label><label>Observação<textarea id="noteText" required></textarea></label></div>`, async () => {
  await api("/api/manager/day-note", { method: "POST", body: JSON.stringify({ user_id: userId, work_date: workDate, category: $("#noteCategory").value, note: $("#noteText").value }) });
  toast("Ocorrência registrada."); await loadManager();
}, true);

window.reviewOvertime = (userId, workDate) => confirmModal("Analisar justificativa", `<div class="form-stack"><p class="legal-note">Esta análise não altera nem remove as horas efetivamente apuradas.</p><label>Análise do motivo<select id="overtimeReviewStatus"><option value="APPROVED">Justificativa validada</option><option value="REJECTED">Justificativa contestada</option></select></label><label>Tratamento administrativo<select id="overtimeTreatment"><option value="PAYMENT">Pagamento</option><option value="TIME_BANK">Banco de horas</option><option value="HR_REVIEW">Encaminhado ao RH</option></select></label><label>Observação<textarea id="overtimeReviewNote" placeholder="Explique a decisão administrativa"></textarea></label></div>`, async () => {
  await api("/api/manager/overtime-review", { method: "POST", body: JSON.stringify({ user_id: userId, work_date: workDate, status: $("#overtimeReviewStatus").value, treatment: $("#overtimeTreatment").value, note: $("#overtimeReviewNote").value }) });
  toast("Análise registrada sem alterar as horas apuradas."); await loadManager();
}, true);

window.managerCorrection = (userId, workDate) => confirmModal("Adicionar marcação corrigida", `<div class="form-stack"><label>Data e hora<input id="managerCorrectionDate" type="datetime-local" value="${workDate}T08:00"></label><label>Tipo<select id="managerCorrectionType">${Object.entries(labels).map(([v,l]) => `<option value="${v}">${l}</option>`).join("")}</select></label><label>Motivo<textarea id="managerCorrectionReason" required minlength="5"></textarea></label></div>`, async () => {
  await api("/api/corrections", { method: "POST", body: JSON.stringify({ user_id: userId, action: "ADD", requested_at_value: new Date($("#managerCorrectionDate").value).toISOString(), requested_type: $("#managerCorrectionType").value, reason: $("#managerCorrectionReason").value }) });
  toast("Correção registrada com auditoria."); await loadManager();
}, true);

function confirmModal(title, content, onConfirm, html = false) {
  $("#modalTitle").textContent = title;
  $("#modalContent")[html ? "innerHTML" : "textContent"] = content;
  $("#modal").classList.remove("hidden");
  $("#modalConfirm").onclick = async () => {
    try { await onConfirm(); $("#modal").classList.add("hidden"); } catch (error) { toast(error.message, true); }
  };
}
$("#modalCancel").addEventListener("click", () => $("#modal").classList.add("hidden"));

boot().catch(() => show("#loginView"));
