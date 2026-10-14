// A lot's dates (module 18, plan H6) on the screen: once a lot has stock its manufacturing and expiry
// dates are shown fixed on its form and a change of them is refused, and QA corrects them on the
// lot's form with a signature, entering the new dates and the reason in the signing dialog
// (meddevice_suite/lot_control/lot_dates.py).
const USERS = "meddevice_suite.qa.ui_users";
const JOURNEY = "meddevice_suite.qa.ui_journey";
const FORM = ".page-container:visible";
const LOT_DATES = ["manufacturing_date", "expiry_date"];

let password, qa, lot, expiry;

function as_user(user) {
	// Leaves the previous person's page first (as in standard_flow.js).
	cy.window({ log: false }).then((win) => win.location.replace("about:blank"));
	cy.location("href", { log: false }).should("eq", "about:blank");
	if (user === "Administrator") cy.login();
	else cy.login(user, password);
	cy.visit("/desk");
	cy.window().its("frappe.session.user").should("eq", user);
}

function open_lot() {
	cy.visit(`/desk/batch/${encodeURIComponent(lot)}`);
	cy.window().should((win) => {
		expect(win.cur_frm?.doctype).to.equal("Batch");
		expect(win.cur_frm.doc.name).to.equal(lot);
		expect(win.cur_frm.doc.__onload?.dates_fixed, "the lot has stock").to.equal(true);
	});
}

// The day `days` after an ISO date, as an ISO date.
function shifted(iso, days) {
	const day = new Date(`${iso}T00:00:00Z`);
	day.setUTCDate(day.getUTCDate() + days);
	return day.toISOString().slice(0, 10);
}

context("Lot dates", () => {
	before(() => {
		cy.login();
		cy.visit("/desk");
		cy.call(`${USERS}.prepare_job_users`).then(({ message }) => {
			password = message.password;
			qa = message.users["QA Manager"];
		});
		cy.then(() => cy.call(`${JOURNEY}.prepare_journey`, { password }));
		cy.then(() =>
			cy.call(`${JOURNEY}.prepare_lot_dates`).then(({ message }) => {
				lot = message.lot;
				expiry = message.expiry_date;
			})
		);
	});

	it("[URS-STK-011] the dates of a lot with stock are shown fixed, and a change through the API is refused", () => {
		as_user(qa); // QA's job writes lots (Item Manager), so only the lot's dates rule stops it
		open_lot();
		cy.window().should((win) => {
			for (const field of LOT_DATES) {
				expect(win.cur_frm.get_docfield(field).read_only, field).to.equal(1);
			}
		});
		cy.window()
			.its("frappe.csrf_token")
			.then((csrf_token) =>
				cy.request({
					method: "POST",
					url: "/api/method/frappe.client.set_value",
					body: {
						doctype: "Batch",
						name: lot,
						fieldname: "expiry_date",
						value: "2099-12-31",
					},
					headers: { Accept: "application/json", "X-Frappe-CSRF-Token": csrf_token },
					failOnStatusCode: false,
				})
			)
			.then((response) => {
				expect(response.status, "refused").to.be.within(400, 499);
				expect(JSON.stringify(response.body)).to.contain("can no longer be edited");
			});
		cy.call("frappe.client.get_value", {
			doctype: "Batch",
			filters: { name: lot },
			fieldname: "expiry_date",
		}).then(({ message }) => expect(message.expiry_date, "unchanged").to.equal(expiry));
	});

	it("[URS-STK-012] QA corrects the dates on the lot's form with a signature, giving the reason", () => {
		const corrected = shifted(expiry, 30);
		const reason = "Supplier's certificate gives a later expiry (browser test)";
		as_user(qa);
		open_lot();
		cy.get(
			`${FORM} button[data-label="${encodeURIComponent("Correct Dates")}"]:visible`
		).click();
		cy.window().should((win) => {
			expect(win.cur_dialog?.display, "the signing dialog has opened").to.equal(true);
			expect(win.cur_dialog.title).to.equal("Sign: Correct Dates");
			expect(
				win.cur_dialog.get_value("expiry_date"),
				"the lot's date to start from"
			).to.equal(expiry);
		});
		// The new date is picked in the dialog's date field, as the person picks it.
		cy.window()
			.its("cur_dialog")
			.then((dialog) => dialog.set_value("expiry_date", corrected));
		cy.window()
			.its("cur_dialog")
			.should((dialog) => expect(dialog.get_value("expiry_date")).to.equal(corrected));
		cy.get('.modal.show textarea[data-fieldname="reason"]').click().type(reason);
		cy.window()
			.its("cur_dialog")
			.should((dialog) => expect(dialog.get_value("reason")).to.equal(reason));
		cy.get('.modal.show input[data-fieldname="password"]')
			.click()
			.type(password, { log: false });
		cy.window()
			.its("cur_dialog")
			.should((dialog) => expect(dialog.get_value("password")).to.equal(password));
		cy.get(".modal:visible .btn-primary").contains("Sign").click();
		cy.window().should((win) => {
			const doc = win.cur_frm.doc;
			expect(doc.expiry_date, "corrected").to.equal(corrected);
			expect(doc.mds_dates_corrected_by).to.equal(qa);
			expect(doc.mds_dates_correction_reason).to.equal(reason);
			const signed = (doc.__onload?.signatures || []).map((s) => [s.action, s.meaning]);
			expect(signed).to.deep.include(["Correct Dates", "Approved"]);
		});
	});
});
