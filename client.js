// Browser-side page interactions; the Python server exposes the API.
document.addEventListener("DOMContentLoaded", async () => {
    const menu = document.querySelector(".menu-button");
    const nav = document.querySelector(".main-nav");
    if (menu && nav) {
        const setMenuOpen = open => {
            nav.classList.toggle("show", open);
            menu.setAttribute("aria-expanded", String(open));
            menu.setAttribute("aria-label", open ? "Close menu" : "Open menu");
        };
        setMenuOpen(false);
        menu.addEventListener("click", () => setMenuOpen(menu.getAttribute("aria-expanded") !== "true"));
        nav.addEventListener("click", event => {
            if (event.target.closest("a")) setMenuOpen(false);
        });
        document.addEventListener("keydown", event => {
            if (event.key === "Escape") setMenuOpen(false);
        });
    }

    const loginQuery = new URLSearchParams(location.search);
    const requestedService = loginQuery.get("service");
    const bookingRequested = loginQuery.get("book") === "1" || requestedService !== null;
    const bookingDestination = new URL("appointment.html", location.href);
    if (requestedService) bookingDestination.searchParams.set("service", requestedService);
    const getLoginDestination = role => {
        if (role === "admin") return "dashboard.html";
        return bookingRequested
            ? `${bookingDestination.pathname.split("/").pop()}${bookingDestination.search}`
            : "user-dashboard.html";
    };

    const loginForm = document.getElementById("loginForm");
    const accountLink = document.querySelector('.main-nav a[href="login.html"]');
    if (loginForm || accountLink) {
        fetch("/api/session")
            .then(async response => {
                if (!response.ok) return;
                const session = await response.json();
                if (!session.authenticated) return;

                if (accountLink) {
                    accountLink.href = session.role === "admin" ? "dashboard.html" : "user-dashboard.html";
                    accountLink.textContent = session.role === "admin" ? "Dashboard" : "My Dashboard";
                }
                if (loginForm) location.replace(getLoginDestination(session.role));
            })
            .catch(error => console.error("Could not check the existing sign-in.", error));
    }

    if (loginForm) {
        loginForm.addEventListener("submit", async event => {
            event.preventDefault();
            const message = document.getElementById("loginMessage");
            try {
                const response = await fetch("/api/login", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        username: document.getElementById("username").value.trim(),
                        password: document.getElementById("password").value
                    })
                });
                const result = await response.json();
                if (!response.ok || !result.success) {
                    message.textContent = result.message || "Invalid username or password.";
                    return;
                }
                location.href = getLoginDestination(result.role);
            } catch (error) {
                message.textContent = "Could not connect to the SQLite server. Double-click Start Luxe Strand.bat and try again.";
            }
        });
    }

    const registerForm = document.getElementById("registerForm");
    const showRegister = document.getElementById("showRegister");
    const showLogin = document.getElementById("showLogin");
    if (loginForm && registerForm) {
        if (showRegister) {
            showRegister.addEventListener("click", event => {
                event.preventDefault();
                loginForm.hidden = true;
                registerForm.hidden = false;
            });
        }
        if (showLogin) {
            showLogin.addEventListener("click", event => {
                event.preventDefault();
                registerForm.hidden = true;
                loginForm.hidden = false;
            });
        }
        registerForm.addEventListener("submit", async event => {
            event.preventDefault();
            const message = document.getElementById("registerMessage");
            try {
                const response = await fetch("/api/register", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        username: document.getElementById("registerUsername").value.trim(),
                        password: document.getElementById("registerPassword").value
                    })
                });
                const result = await response.json();
                if (!response.ok || !result.success) {
                    message.textContent = result.message || "Unable to create the account.";
                    return;
                }
                location.href = getLoginDestination("customer");
            } catch (error) {
                message.textContent = "Could not connect to the SQLite server. Double-click Start Luxe Strand.bat and try again.";
            }
        });
    }

    const logoutLink = document.getElementById("logoutLink");
    if (logoutLink) {
        logoutLink.addEventListener("click", async event => {
            event.preventDefault();
            try {
                await fetch("/api/logout", { method: "POST" });
            } finally {
                location.href = "login.html";
            }
        });
    }

    if (document.body.dataset.page === "dashboard" ||
        document.body.dataset.page === "user-dashboard") {
        const pageRole = document.body.dataset.page === "dashboard" ? "admin" : "customer";
        try {
            const response = await fetch("/api/session");
            if (!response.ok) {
                location.href = "login.html";
                return;
            }
            const session = await response.json();
            if (session.role !== pageRole) {
                location.href = session.role === "admin" ? "dashboard.html" : "user-dashboard.html";
                return;
            }
        } catch (error) {
            document.getElementById("appointmentRows").innerHTML =
                '<tr><td colspan="6" class="empty">Unable to verify account access.</td></tr>';
            return;
        }

        const isAdmin = pageRole === "admin";
        const rows = document.getElementById("appointmentRows");
        if (isAdmin) {
            rows.addEventListener("click", async event => {
                const button = event.target.closest(".appointment-remove");
                if (!button) return;

                if (!window.confirm("Remove this appointment? This action cannot be undone.")) return;

                const appointmentId = Number(button.dataset.appointmentId);
                button.disabled = true;
                const message = document.getElementById("adminAppointmentMessage");
                message.textContent = "";
                try {
                    const response = await fetch("/api/appointments", {
                        method: "DELETE",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ id: appointmentId })
                    });
                    const result = await response.json();
                    if (!response.ok || !result.success) {
                        throw new Error(result.message || "Appointment could not be removed.");
                    }
                    message.textContent = result.message;
                    await renderAppointments(isAdmin);
                } catch (error) {
                    button.disabled = false;
                    message.textContent = error.message;
                }
            });

            rows.addEventListener("change", async event => {
                const select = event.target.closest(".appointment-status");
                if (!select) return;

                const previousStatus = select.dataset.currentStatus;
                select.disabled = true;
                const message = document.getElementById("adminAppointmentMessage");
                message.textContent = "";
                try {
                    const response = await fetch("/api/appointments/status", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            id: Number(select.dataset.appointmentId),
                            status: select.value
                        })
                    });
                    const result = await response.json();
                    if (!response.ok || !result.success) {
                        throw new Error(result.message || "Appointment status could not be updated.");
                    }
                    message.textContent = `Appointment marked ${result.status.toLowerCase()}.`;
                    await renderAppointments(isAdmin);
                } catch (error) {
                    select.value = previousStatus;
                    select.disabled = false;
                    message.textContent = error.message;
                }
            });
        }

        await renderAppointments(isAdmin);
        const form = document.getElementById("appointmentForm");
        if (form) {
            const dateInput = document.getElementById("appointmentDate");
            dateInput.min = getLocalDate();
            dateInput.max = getYearEnd();
            form.addEventListener("submit", async event => {
            event.preventDefault();
            const customer = document.getElementById("customer").value.trim();
            const data = {
                customer,
                service: document.getElementById("service").value,
                date: dateInput.value,
                time: document.getElementById("appointmentTime").value
            };
            const message = document.getElementById("adminAppointmentMessage");
            if (!/^[A-Za-z0-9_.-]{3,32}$/.test(customer)) {
                message.textContent = "Use a username-style name without spaces or last names.";
                return;
            }
            if (isPastAppointment(data.date, data.time)) {
                message.textContent = "Choose a future date and time.";
                return;
            }
            try {
                const response = await fetch("/api/appointments", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(data)
                });
                const result = await response.json();
                if (!response.ok || !result.success) {
                    throw new Error(result.message || "Appointment could not be added.");
                }
                form.reset();
                dateInput.min = getLocalDate();
                dateInput.max = getYearEnd();
                message.textContent = "Appointment added.";
                await renderAppointments(isAdmin);
            } catch (error) {
                message.textContent = error.message;
            }
            });
        }
    }

    const bookingForm = document.getElementById("bookingForm");
    if (bookingForm) {
        const dateInput = document.getElementById("bookingDate");
        const message = document.getElementById("bookingMessage");
        const requestedService = new URLSearchParams(location.search).get("service");
        if (requestedService) document.getElementById("bookingService").value = requestedService;
        dateInput.min = getLocalDate();
        dateInput.max = getYearEnd();
        document.getElementById("bookingPhone").pattern = "(?:\\+63|0)9(?:(?: |-)?\\d){9}";
        document.getElementById("bookingPhone").title = "Enter a Philippine mobile number, such as 09XX XXX XXXX or +63 9XX XXX XXXX.";
        bookingForm.addEventListener("submit", async event => {
            event.preventDefault();
            const customer = document.getElementById("bookingName").value.trim();
            const data = {
                customer,
                phone: document.getElementById("bookingPhone").value.trim(),
                service: document.getElementById("bookingService").value,
                date: dateInput.value,
                time: document.getElementById("bookingTime").value
            };
            if (!/^[A-Za-z0-9_.-]{3,32}$/.test(customer)) {
                message.textContent = "Use a username-style name without spaces or last names.";
                return;
            }
            if (isPastAppointment(data.date, data.time)) {
                message.textContent = "Choose a future date and time.";
                return;
            }
            try {
                const response = await fetch("/api/appointments", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(data)
                });
                const result = await response.json();
                if (!response.ok || !result.success) {
                    throw new Error(result.message || "Your appointment could not be saved.");
                }
                bookingForm.reset();
                dateInput.min = getLocalDate();
                dateInput.max = getYearEnd();
                message.textContent = result.message ||
                    "Your appointment request is saved and is pending salon confirmation.";
            } catch (error) {
                message.textContent = error.message;
            }
        });
    }
});

async function renderAppointments(isAdmin = false) {
    const rows = document.getElementById("appointmentRows");
    const total = document.getElementById("totalAppointments");
    const pending = document.getElementById("pendingAppointments");
    try {
        const response = await fetch("/api/appointments");
        const data = await response.json();
        if (!response.ok || !Array.isArray(data.appointments)) {
            throw new Error("Appointments could not be loaded.");
        }
        const items = data.appointments;
        rows.innerHTML = items.length
            ? items.map(appointment => {
                const statusCell = isAdmin
                    ? `<select class="appointment-status" data-appointment-id="${appointment.id}" data-current-status="${escapeHtml(appointment.status)}" aria-label="Status for ${escapeHtml(appointment.customer)}"><option value="Pending"${appointment.status === "Pending" ? " selected" : ""}>Pending</option><option value="Completed"${appointment.status === "Completed" ? " selected" : ""}>Completed</option></select>`
                    : escapeHtml(appointment.status);
                const statusAndActions = isAdmin
                    ? `${statusCell}<button type="button" class="appointment-remove" data-appointment-id="${appointment.id}" aria-label="Remove appointment for ${escapeHtml(appointment.customer)}">Remove</button>`
                    : statusCell;
                return `<tr><td>${escapeHtml(appointment.customer)}</td><td>${escapeHtml(appointment.phone || "—")}</td><td>${escapeHtml(appointment.service)}</td><td>${escapeHtml(appointment.date)}</td><td>${escapeHtml(appointment.time)}</td><td>${statusAndActions}</td></tr>`;
            }).join("")
            : `<tr><td colspan="6" class="empty">No appointments yet.</td></tr>`;
        if (total) total.textContent = String(items.length);
        if (pending) {
            pending.textContent = String(items.filter(item => item.status === "Pending").length);
        }
    } catch (error) {
        rows.innerHTML = '<tr><td colspan="6" class="empty">Unable to load appointments.</td></tr>';
        if (total) total.textContent = "0";
        if (pending) pending.textContent = "0";
    }
}

function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, character => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#039;"
    })[character]);
}

function getLocalDate() {
    const today = new Date();
    const offset = today.getTimezoneOffset();
    return new Date(today.getTime() - offset * 60000).toISOString().slice(0, 10);
}

function getYearEnd() {
    return `${new Date().getFullYear()}-12-31`;
}

function isPastAppointment(date, time) {
    return new Date(`${date}T${time}`).getTime() < Date.now();
}
