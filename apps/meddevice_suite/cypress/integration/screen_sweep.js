// Signed in as each job, the browser opens every area of that job's desk, the list and the
// new form of every record type the job may create, and searches every lookup in those
// forms as the field itself would (URS-PLT-009). The live pilot found refusals that tests
// run as Administrator, or that create records through code, never see; this sweep finds
// the next one. What each job reaches is decided on the server from Frappe's own
// permissions (meddevice_suite/qa/ui_users.py); this spec drives the browser and collects
// every refused or failed request, script error and error message along the way.
//
// An area is a workspace page, or a sidebar of the desk without one (Organization,
// Documents): the sweep opens every workspace, and every list and query or script report
// the sidebars of the job's desk icons link to (URS-PLT-008).
//
// Lookups left closed on purpose are not searched: the server hands over the reviewed list
// (meddevice_suite/setup/lookups.py REVIEWED_CLOSED), which setup/test_lookups.py holds exact.

// meddevice_suite.setup.roles.JOB_PROFILES. The sweep stops when the two differ, so a new
// job is never silently left out.
const JOBS = [
	"Sales",
	"Purchase",
	"Stores",
	"Production Supervisor",
	"Operator",
	"QC Inspector",
	"QA Manager",
	"Maintenance",
	"Training Coordinator",
	"Accounts",
	"Management",
	"System Administrator",
];

const HELPERS = "meddevice_suite.qa.ui_users";
const PAGE_TIMEOUT = 20000; // the longest a page may take to open and finish loading
const QUIET_MS = 300; // no request in flight for this long: the page has finished loading
const PARALLEL_SEARCHES = 6; // lookups searched at once, as several fields would
const API = /^https?:\/\/[^/]+\/api\//;

// Errors of Frappe, ERPNext and India Compliance themselves, which every user meets, the
// Administrator included (checked on 2026-10-07 by opening the same pages as Administrator),
// and which no permission or configuration of ours can change; each is harmless to records
// and was reviewed that day. Only the stated answer on the stated page is accepted: any
// other error there, or the same error elsewhere, still fails the sweep. A fix belongs in
// the owning app (or a form script of ours) and then its entry is removed.
const SEARCH = "/api/method/frappe.desk.search.search_link";
const ANY_REPORT = "any report";
const QUIRKS = [
	// A new customer or supplier has no addresses or contacts yet: the field's own query
	// (get_customer_primary, get_supplier_primary) looks the unsaved record up by its
	// temporary name and answers Not Found. Once the record is saved the lookup works.
	...["customer_primary_address", "customer_primary_contact"].map((field) => ({
		where: "new Customer",
		path: SEARCH,
		args: { reference_doctype: "Customer", link_fieldname: field },
		status: 404,
		exc_type: "DoesNotExistError",
	})),
	...["supplier_primary_address", "supplier_primary_contact"].map((field) => ({
		where: "new Supplier",
		path: SEARCH,
		args: { reference_doctype: "Supplier", link_fieldname: field },
		status: 404,
		exc_type: "DoesNotExistError",
	})),
	// A maintenance task is a row of its asset maintenance, and Frappe searches no table
	// row directly. The form sets the task's own query only once the asset maintenance is
	// chosen (asset_maintenance_log.js); that query is open to the job
	// (setup/test_lookups.py). Before then the plain search is refused.
	{
		where: "new Asset Maintenance Log",
		path: SEARCH,
		args: { reference_doctype: "Asset Maintenance Log", link_fieldname: "task" },
		status: 403,
		exc_type: "PermissionError",
	},
	// The report requires From Date on the server but its filter has neither a default nor
	// a required mark, so it runs on opening and says "From Date is mandatory".
	{
		where: "report Sales Pipeline Analytics",
		path: "/api/method/frappe.desk.query_report.run",
		status: 417,
		exc_type: "ValidationError",
	},
	// The same for lot traceability: the server asks for an item, batch or serial number,
	// which the filters neither require nor default, before the person can choose one.
	{
		where: "report Serial No and Batch Traceability",
		path: "/api/method/frappe.desk.query_report.run",
		status: 417,
		exc_type: "ValidationError",
	},
	// The GST purchase reconciliation tool asks for the date range of its period before
	// its own onload has set the period, and the server refuses the empty period
	// (india_compliance purchase_reconciliation_tool.js, setup before onload).
	{
		where: "list Purchase Reconciliation Tool",
		path: "/api/method/run_doc_method",
		args: { method: "get_date_range_and_check_missing_documents" },
		status: 417,
		exc_type: "FrappeTypeError",
	},
	{
		where: "list Purchase Reconciliation Tool",
		script_error: "An unknown error has occurred: [object Object]",
	},
	// Frappe raises this while it redraws a report's chart (desk.bundle, ResizeObserver
	// boundDrawFn), on any report once it has data, for the Administrator too; the report
	// opens. Accepted with this exact message on report pages only.
	{
		where: ANY_REPORT,
		script_error:
			"Failed to execute 'removeChild' on 'Node': The node to be removed is not a child of this node.",
	},
];

function known_quirk(sweep, matches) {
	const applies = (entry) =>
		entry.where === sweep.where ||
		(entry.where === ANY_REPORT && sweep.where.startsWith("report "));
	const quirk = QUIRKS.find((entry) => applies(entry) && matches(entry));
	if (quirk) sweep.quirks.push(`${sweep.where}: ${quirk.script_error || quirk.path}`);
	return Boolean(quirk);
}

// The runtime state of one job's sweep.
function new_sweep(job) {
	return {
		job,
		where: "sign-in", // the page being checked, named in every failure
		failures: [],
		answered: new Set(), // pages with a refused or failed request recorded, or a quirk
		quirks: [],
		notes: [],
		searched: [],
		unsearched: [],
		started: Date.now(),
	};
}

// Signs in without writing the password into the command log or screenshots.
function sign_in(email, password) {
	cy.session(["job", email], () => {
		cy.request({
			url: "/api/method/login",
			method: "POST",
			body: { usr: email, pwd: password },
			log: false,
		});
	});
}

function plain(html) {
	const text = String(html ?? "")
		.replace(/<[^>]*>/g, " ")
		.replace(/\s+/g, " ")
		.trim();
	return text.length > 300 ? `${text.slice(0, 300)}…` : text;
}

// The arguments that say what a refused request was for.
const NAMED_ARGUMENTS = [
	"doctype",
	"reference_doctype",
	"link_fieldname",
	"query",
	"method",
	"fieldname",
	"field",
	"report_name",
	"chart_name",
];

function decoded(url) {
	try {
		return decodeURIComponent(url.replace(/\+/g, " "));
	} catch {
		return url;
	}
}

function request_args(body) {
	if (!body) return {};
	if (typeof body === "object") return body;
	try {
		return JSON.parse(body);
	} catch {
		return Object.fromEntries(new URLSearchParams(body));
	}
}

function server_message(body) {
	const response = request_args(body);
	const messages = [];
	if (response.exc_type) messages.push(response.exc_type);
	try {
		for (const message of JSON.parse(response._server_messages || "[]")) {
			messages.push(plain(JSON.parse(message).message));
		}
	} catch {
		// not Frappe's message list: the status says enough
	}
	return messages.join(": ");
}

// The arguments of a request: its query string and its body.
function arguments_of(req) {
	const query = Object.fromEntries(new URL(req.url).searchParams);
	return { ...query, ...request_args(req.body) };
}

// Records a refused or failed request, unless it is a reviewed ERPNext quirk.
function record_response(sweep, req, res) {
	if (res.statusCode < 400) return;
	sweep.answered.add(sweep.where);
	const args = arguments_of(req);
	const exc_type = request_args(res.body).exc_type;
	const quirk = known_quirk(
		sweep,
		(entry) =>
			entry.path === new URL(req.url).pathname &&
			entry.status === res.statusCode &&
			entry.exc_type === exc_type &&
			Object.entries(entry.args || {}).every(([key, value]) => args[key] === value)
	);
	if (!quirk) sweep.failures.push(describe_failure(sweep.where, req, res));
}

function describe_failure(where, req, res) {
	const args = arguments_of(req);
	const named = NAMED_ARGUMENTS.filter((key) => args[key])
		.map((key) => `${key}=${plain(args[key])}`)
		.join(", ");
	const url = decoded(new URL(req.url).pathname);
	const message = server_message(res.body);
	return `${where}: ${req.method} ${res.statusCode} ${url}${named ? ` {${named}}` : ""}${
		message ? ` → ${message}` : ""
	}`;
}

// Records an error the page's own code did not handle. Cypress wraps it in an explanation;
// the error itself is on the lines it quotes ("> …").
function record_script_error(sweep, error) {
	const quoted = String(error.message)
		.split("\n")
		.filter((line) => line.trim().startsWith(">"))
		.map((line) => line.trim().slice(1).trim());
	const message = quoted.length ? quoted.join(" ") : String(error.message).split("\n")[0];
	// Frappe's query report stops loading a report whose required filter has no default by
	// throwing "Filter missing", having shown "Please set filters" on the page
	// (frappe/public/js/frappe/views/reports/query_report.js get_filter_values), for every
	// user. The person sets the filter; nothing has failed.
	if (sweep.where.startsWith("report ") && message === "Filter missing") {
		sweep.quirks.push(`${sweep.where}: asks for its filters`);
	} else if (!known_quirk(sweep, (entry) => entry.script_error === message)) {
		sweep.failures.push(`${sweep.where}: script error: ${message}`);
	}
	return false; // keep sweeping; the failures are reported together at the end
}

// Resolves true once `ready()` holds, false when it does not within the page timeout.
function until(ready, timeout = PAGE_TIMEOUT) {
	return new Promise((resolve) => {
		const started = Date.now();
		const check = () => {
			let done = false;
			try {
				done = Boolean(ready());
			} catch {
				done = false;
			}
			if (done) return resolve(true);
			if (Date.now() - started > timeout) return resolve(false);
			setTimeout(check, 50);
		};
		check();
	});
}

// Resolves once no request has been in flight for QUIET_MS.
function quiet(win) {
	let since = null;
	return until(() => {
		if (win.frappe.request.ajax_count > 0) {
			since = null;
			return false;
		}
		since = since ?? Date.now();
		return Date.now() - since >= QUIET_MS;
	});
}

// Waits for the page to open and finish loading, then records what went wrong on it.
async function arrive(win, sweep, opened) {
	const ok = await until(() => opened());
	const settled = await quiet(win);
	if (!ok) {
		sweep.failures.push(
			`${sweep.where}: did not open; the browser is at ${win.location.pathname}`
		);
	}
	if (!settled) {
		sweep.failures.push(`${sweep.where}: still loading after ${PAGE_TIMEOUT / 1000} s`);
	}
	// An error message the person would see. One that reports a request of this page
	// already recorded (refused, or a known quirk) is not counted twice.
	const dialog = win.frappe.msg_dialog;
	if (dialog && dialog.$wrapper.is(":visible")) {
		const title = plain(dialog.$wrapper.find(".modal-title").text());
		const text = plain(dialog.$wrapper.find(".msgprint").text());
		const shown = `${sweep.where}: shows the message "${title}: ${text}"`;
		// Frappe marks errors and refusals red and warnings orange; a plain message
		// ("No record found") informs the person and is kept as a note.
		if (!/\b(red|orange)\b/.test(dialog.indicator?.attr("class") || "")) {
			sweep.notes.push(shown);
		} else if (!sweep.answered.has(sweep.where)) {
			sweep.failures.push(shown);
		}
		await close_message(win);
	}
	return ok;
}

// Closes the message so the next page starts without it. Bootstrap ignores a hide while
// the dialog is still opening, so it is asked again until the dialog has gone.
async function close_message(win) {
	const dialog = win.frappe.msg_dialog;
	const closed = await until(() => {
		if (!dialog.$wrapper.is(":visible")) return true;
		win.frappe.hide_msgprint(true);
		return false;
	}, 5000);
	if (!closed) throw new Error(`the message "${plain(dialog.$wrapper.text())}" does not close`);
}

function in_browser(callback) {
	return cy.window({ log: false }).then({ timeout: 4 * PAGE_TIMEOUT }, callback);
}

function open_area(sweep, area) {
	cy.window({ log: false }).then((win) => {
		sweep.where = `area ${area}`;
		cy.visit(`/desk/${win.frappe.router.slug(area)}`);
	});
	in_browser((win) =>
		arrive(win, sweep, () => {
			const route = win.frappe.get_route();
			return route[0] === "Workspaces" && route[route.length - 1] === area;
		})
	);
}

function open_list(sweep, doctype) {
	in_browser((win) => {
		sweep.where = `list ${doctype}`;
		win.frappe.set_route(`/desk/${win.frappe.router.slug(doctype)}`);
		return arrive(win, sweep, () => win.frappe.get_route()[1] === doctype);
	});
}

function open_report(sweep, report) {
	in_browser((win) => {
		sweep.where = `report ${report}`;
		win.frappe.set_route(["query-report", report]);
		return arrive(win, sweep, () => {
			const route = win.frappe.get_route();
			return (
				route[0] === "query-report" &&
				route[1] === report &&
				win.frappe.query_report?.report_name === report
			);
		});
	});
}

function open_new_form(sweep, doctype, closed) {
	in_browser(async (win) => {
		sweep.where = `new ${doctype}`;
		win.frappe.set_route(`/desk/${win.frappe.router.slug(doctype)}/new`);
		const opened = await arrive(win, sweep, () => {
			const route = win.frappe.get_route();
			const frm = win.cur_frm;
			return (
				route[0] === "Form" &&
				route[1] === doctype &&
				frm?.doctype === doctype &&
				frm.doc?.__islocal &&
				frm.doc.name === route[2]
			);
		});
		if (!opened) return;

		const frm = win.cur_frm;
		if (!Object.values(frm.fields_dict).some((field) => fillable(win, field))) {
			sweep.failures.push(`${sweep.where}: has no field this job can fill in`);
		}
		await search_lookups(win, sweep, lookups_of(win, frm, sweep, closed));
		await arrive(win, sweep, () => true);
	});
}

function fillable(win, field) {
	const df = field.df;
	return (
		df &&
		field.disp_status === "Write" &&
		(!win.frappe.model.no_value_type.includes(df.fieldtype) ||
			win.frappe.model.table_fields.includes(df.fieldtype))
	);
}

// Whether the person can fill in this field of a table row: as the row shows it
// (frappe/public/js/frappe/form/grid_row.js evaluates depends_on and read_only_depends_on
// against the row and its parent), and as the job's permissions allow.
function fillable_in_row(win, df, row, frm) {
	const holds = (expression) => {
		if (typeof expression === "boolean") return expression;
		if (typeof expression === "function") return expression(row);
		if (expression.startsWith("eval:")) {
			return win.frappe.utils.eval(expression.slice(5), { doc: row, parent: frm.doc });
		}
		if (expression.startsWith("fn:")) return true; // the form decides; searched as shown
		const value = row[expression];
		return Array.isArray(value) ? value.length > 0 : Boolean(value);
	};
	if (df.depends_on && !holds(df.depends_on)) return false;
	if (df.read_only_depends_on && holds(df.read_only_depends_on)) return false;
	return win.frappe.perm.get_field_display_status(df, row, frm.perm) === "Write";
}

// Every lookup the job can fill in on the form: its own link fields, and the link fields
// of a row in each of its tables, each with the arguments the field itself would search with.
function lookups_of(win, frm, sweep, closed) {
	const lookups = [];
	const add = (df, form, search_args) => {
		const label = `${form}.${df.fieldname}`;
		if (closed.has(`${df.options}|${label}`)) {
			sweep.unsearched.push(`${label} (${df.options})`);
		} else {
			lookups.push({ label, doctype: df.options, search_args });
		}
	};
	for (const field of Object.values(frm.fields_dict)) {
		const df = field.df;
		if (!df || field.disp_status !== "Write") continue;
		if (df.fieldtype === "Link" && df.options) {
			add(df, frm.doctype, () => field.get_search_args(""));
		} else if (df.fieldtype === "Table" && field.grid) {
			const grid = field.grid;
			const links = (grid.docfields || []).filter(
				(child_df) => child_df.fieldtype === "Link" && child_df.options
			);
			if (!links.length) continue;
			// A fresh row, as the person adds one before filling it in.
			const row = win.frappe.model.add_child(frm.doc, df.options, df.fieldname);
			for (const child_df of links) {
				if (!fillable_in_row(win, child_df, row, frm)) continue;
				add(child_df, df.options, () => {
					// As a table row makes its field (frappe/public/js/frappe/form/grid_row.js).
					const control = win.frappe.ui.form.make_control({
						df: child_df,
						parent: win.$("<div>"),
						only_input: true,
						with_link_btn: true,
						doc: row,
						doctype: row.doctype,
						docname: row.name,
						frm,
						grid,
					});
					control.get_query = grid.get_field(child_df.fieldname).get_query;
					return control.get_search_args("");
				});
			}
		}
	}
	return lookups;
}

async function search_lookups(win, sweep, lookups) {
	const queue = [...lookups];
	const search = async () => {
		while (queue.length) {
			const lookup = queue.shift();
			// The form's own filter for a field may ask for another field first ("enter the
			// item code first"); the person sees that hint, not a refusal, and the field is
			// searched once that field is filled in. The hint is kept as a note.
			const hints = [];
			const msgprint = win.frappe.msgprint;
			win.frappe.msgprint = (message) => {
				hints.push(plain(message?.message ?? message));
			};
			let args;
			try {
				args = lookup.search_args();
			} catch (error) {
				hints.push(plain(error?.message ?? error));
			} finally {
				win.frappe.msgprint = msgprint;
			}
			if (hints.length) {
				sweep.notes.push(`${sweep.where}: ${lookup.label} first asks: ${hints[0]}`);
				continue;
			}
			if (!args) continue;
			sweep.searched.push(`${lookup.label} (${lookup.doctype})`);
			try {
				await win.frappe.xcall("frappe.desk.search.search_link", args, "POST", {
					no_spinner: true,
				});
			} catch {
				// recorded with the server's answer by the request log
			}
		}
	};
	await Promise.all(Array.from({ length: PARALLEL_SEARCHES }, search));
}

// What each job's sweep reached and found, kept as test evidence (reports/ is a CI artifact).
function record_evidence(sweep) {
	const reach = sweep.reach || {};
	const evidence = {
		job: sweep.job,
		seconds: Math.round((Date.now() - sweep.started) / 1000),
		last_page: sweep.where,
		areas: reach.areas,
		lists: reach.lists,
		reports: reach.reports,
		new_forms: reach.create,
		lookups_searched: sweep.searched,
		lookups_closed_on_purpose: sweep.unsearched,
		erpnext_quirks: sweep.quirks,
		notes: sweep.notes,
		failures: sweep.failures,
	};
	const file = sweep.job.toLowerCase().replace(/ /g, "_");
	cy.writeFile(`reports/screen_sweep/${file}.json`, evidence, { log: false });
}

context("Every job's own screens", () => {
	let password;
	let users;
	let sweep;

	before(() => {
		cy.login();
		cy.visit("/desk");
		cy.call(`${HELPERS}.prepare_job_users`).then(({ message }) => {
			password = message.password;
			users = message.users;
			expect(Object.keys(users).sort(), "jobs").to.deep.equal([...JOBS].sort());
		});
	});

	// Also when a job's test stops early, so the evidence shows how far it got.
	afterEach(() => {
		if (sweep) record_evidence(sweep);
	});

	for (const job of JOBS) {
		it(`[URS-PLT-009] ${job}: every area, list and form of its work opens without an error`, () => {
			sweep = new_sweep(job);
			sign_in(users[job], password);
			cy.intercept({ url: API }, (req) => {
				req.on("response", (res) => record_response(sweep, req, res));
			});
			cy.on("uncaught:exception", (error) => record_script_error(sweep, error));

			sweep.where = "desk";
			cy.visit("/desk");
			in_browser((win) => arrive(win, sweep, () => win.frappe.container?.page));
			cy.call(`${HELPERS}.reachable`).then(({ message: reach }) => {
				sweep.reach = reach;
				expect(reach.areas, "areas of the desk").not.to.be.empty;
				const closed = new Set(
					reach.closed_lookups.map(([doctype, field]) => `${doctype}|${field}`)
				);
				reach.areas.forEach((area) => open_area(sweep, area));
				reach.lists.forEach((doctype) => open_list(sweep, doctype));
				reach.reports.forEach((report) => open_report(sweep, report));
				reach.create.forEach((doctype) => open_new_form(sweep, doctype, closed));
			});
			cy.then(() => {
				sweep.failures.forEach((failure) => cy.log(failure));
				expect(
					sweep.failures,
					`${sweep.failures.length} problems as ${job}:\n${sweep.failures.join("\n")}\n`
				).to.be.empty;
			});
		});
	}
});
