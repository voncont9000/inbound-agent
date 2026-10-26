import { describe, expect, it } from "vitest";
import { APP_URL } from "./config";
import { HERO_HEADLINE } from "./content/hero";
import { render } from "./entry-server";

describe("entry-server prerendering", () => {
  it("renders the home headline and navigation links", () => {
    const { html } = render("/");
    const document = new DOMParser().parseFromString(html, "text/html");
    const heading = document.querySelector("h1");
    const hrefs = Array.from(document.querySelectorAll("a"), (link) => link.getAttribute("href"));

    expect(heading?.textContent?.trim()).toBe(HERO_HEADLINE);
    expect(hrefs).toContain("#product");
    expect(hrefs).toContain("#use-cases");
    expect(hrefs).toContain("#integrations");
    expect(hrefs).toContain("#faq");
    expect(hrefs).toContain(`${APP_URL}/help`);
    expect(hrefs).toContain(`${APP_URL}/login`);
    expect(hrefs).toContain(`${APP_URL}/signup`);
  });

  it("marks privacy and imprint pages noindex and gives the home page a canonical URL", () => {
    expect(render("/privacy").head).toContain('<meta name="robots" content="noindex">');
    expect(render("/imprint").head).toContain('<meta name="robots" content="noindex">');
    expect(render("/").head).toContain(
      '<link rel="canonical" href="https://yelvo.vinci-labs.tech/">',
    );
  });
});
