/* @ds-bundle: {"format":4,"namespace":"Yelvo","components":[{"name":"Button"},{"name":"Badge"},{"name":"Eyebrow"},{"name":"TestimonialCard"},{"name":"StepItem"},{"name":"Accordion"},{"name":"UrlInput"},{"name":"TrustBadge"}]} */
(function () {
  var React = window.React, h = React.createElement;
  function cx() { return Array.prototype.filter.call(arguments, Boolean).join(" "); }
  function svg(d, extra) { return h("svg", Object.assign({ viewBox: "0 0 16 16", fill: "none", stroke: "currentColor", strokeWidth: 1.6, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": "true" }, extra || {}), h("path", { d: d })); }
  var ICON = {
    arrow: "M3 8h10M9 4l4 4-4 4",
    check: "M3 8.5l3 3 7-7",
    plus: "M8 3v10M3 8h10"
  };

  function Button(p) {
    var variant = p.variant || "outline", size = p.size || "sm";
    var rest = Object.assign({}, p); delete rest.variant; delete rest.size; delete rest.icon; delete rest.className; delete rest.href;
    var kids = [p.children, p.icon === "arrow" ? h(React.Fragment, { key: "i" }, svg(ICON.arrow)) : null];
    var cls = cx("dt-btn", "dt-btn-" + variant, "dt-btn-" + size, p.className);
    return p.href ? h("a", Object.assign({ href: p.href, className: cls }, rest), kids) : h("button", Object.assign({ type: "button", className: cls }, rest), kids);
  }

  function Badge(p) { return h("span", { className: cx("dt-badge", "dt-badge-" + (p.tone || "accent")) }, p.children); }

  function Eyebrow(p) { return h("span", { className: "dt-eyebrow" }, h("span", { className: "dt-eyebrow-dot", "aria-hidden": "true" }), p.children); }

  function initials(n) { return String(n || "").split(/\s+/).map(function (w) { return w[0]; }).slice(0, 2).join("").toUpperCase(); }
  function TestimonialCard(p) {
    return h("figure", { className: "dt-quote", style: { margin: 0 } },
      h("blockquote", null, p.quote),
      h("figcaption", { className: "dt-quote-who" },
        p.avatar ? h("img", { className: "dt-quote-avatar", src: p.avatar, alt: "" }) : h("span", { className: "dt-quote-avatar", "aria-hidden": "true" }, initials(p.name)),
        h("span", null, h("div", { className: "dt-quote-name" }, p.name), h("div", { className: "dt-quote-role" }, p.role))));
  }

  function StepItem(p) {
    var n = String(p.number).padStart(2, "0");
    return h("div", { className: "dt-step" }, h("span", { className: "dt-step-num" }, n), h("h3", null, p.title), p.children ? h("p", null, p.children) : null);
  }

  function Accordion(p) {
    var items = p.items || [];
    var st = React.useState(p.defaultOpen == null ? -1 : p.defaultOpen), open = st[0], setOpen = st[1];
    return h("div", { className: "dt-acc" }, items.map(function (it, i) {
      var isOpen = open === i, id = "dt-acc-" + i;
      return h("div", { key: i, className: "dt-acc-item", "data-open": String(isOpen) },
        h("button", { className: "dt-acc-trigger", "aria-expanded": isOpen, "aria-controls": id, onClick: function () { setOpen(isOpen ? -1 : i); } },
          it.question, svg(ICON.plus, { className: "dt-acc-icon" })),
        isOpen ? h("div", { id: id, className: "dt-acc-panel", role: "region" }, it.answer) : null);
    }));
  }

  function UrlInput(p) {
    var st = React.useState(""), v = st[0], setV = st[1];
    return h("form", { onSubmit: function (e) { e.preventDefault(); if (p.onSubmit) p.onSubmit(v); } },
      h("label", { className: "dt-url" },
        h("span", { style: { position: "absolute", width: 1, height: 1, overflow: "hidden", clip: "rect(0 0 0 0)" } }, p.label || "Your website"),
        h("input", { type: "url", value: v, placeholder: p.placeholder || "Your website", onChange: function (e) { setV(e.target.value); } }),
        h(Button, { variant: "cta", size: "lg", type: "submit", icon: "arrow" }, p.action || "Create my agent")),
      p.hints && p.hints.length ? h("ul", { className: "dt-url-hints" }, p.hints.map(function (t, i) { return h("li", { key: i }, svg(ICON.check), t); })) : null);
  }

  function TrustBadge(p) {
    var star = "M8 1.5l1.9 4 4.3.5-3.2 3 .9 4.3L8 11.2 4.1 13.3 5 9 1.8 6l4.3-.5z";
    var faces = ["var(--accent)", "var(--accent-soft)", "var(--accent-hover)"];
    return h("div", { className: "dt-trust" },
      h("span", { className: "dt-trust-faces", "aria-hidden": "true" }, faces.map(function (c, i) { return h("span", { key: i, style: { background: c } }); })),
      h("span", null,
        h("div", { className: "dt-trust-text" }, p.count || "10,000+ customers"),
        h("div", { className: "dt-trust-sub" },
          h("span", { className: "dt-stars", role: "img", "aria-label": (p.rating || 5) + " out of 5" }, [0, 1, 2, 3, 4].map(function (i) { return svg(star, { key: i, fill: "currentColor", stroke: "none" }); })),
          " ", p.source || "Trustpilot and Google")));
  }

  window.Yelvo = Object.assign(window.Yelvo || {}, { Button: Button, Badge: Badge, Eyebrow: Eyebrow, TestimonialCard: TestimonialCard, StepItem: StepItem, Accordion: Accordion, UrlInput: UrlInput, TrustBadge: TrustBadge });
})();
