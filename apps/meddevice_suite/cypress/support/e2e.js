Cypress.Commands.add(
	"login",
	(user = "Administrator", password = Cypress.env("adminPassword")) => {
		cy.session([user, password], () => {
			cy.request({
				url: "/api/method/login",
				method: "POST",
				body: { usr: user, pwd: password },
			});
		});
	}
);

// Server calls as the logged-in user; a desk page must be open for its CSRF token.
function post(url, body, accept_status = [200]) {
	return cy
		.window()
		.its("frappe.csrf_token")
		.then((csrf_token) =>
			cy.request({
				method: "POST",
				url,
				body,
				headers: { Accept: "application/json", "X-Frappe-CSRF-Token": csrf_token },
				failOnStatusCode: false,
			})
		)
		.then((response) => {
			expect(accept_status, JSON.stringify(response.body)).to.include(response.status);
			return response.body;
		});
}

Cypress.Commands.add("call", (method, args = {}) => post(`/api/method/${method}`, args));

Cypress.Commands.add("insert_doc", (doctype, values, ignore_duplicate = false) =>
	post(`/api/resource/${doctype}`, values, ignore_duplicate ? [200, 409] : [200]).then(
		(body) => body.data
	)
);
