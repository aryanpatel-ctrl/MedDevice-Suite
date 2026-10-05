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
