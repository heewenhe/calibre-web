/* Mobile facet drawer: the filter groups are rendered inline on desktop and
   become a bottom sheet below the small breakpoint. */
(function () {
    "use strict";

    function setOpen(open) {
        document.body.classList.toggle("facet-drawer-open", open);
        document.querySelectorAll("[data-facet-drawer-toggle]").forEach(function (el) {
            el.setAttribute("aria-expanded", open ? "true" : "false");
        });
        document.querySelectorAll(".facet-backdrop").forEach(function (el) {
            el.hidden = !open;
        });
    }

    document.addEventListener("click", function (event) {
        if (event.target.closest("[data-facet-drawer-toggle]")) {
            event.preventDefault();
            setOpen(true);
            var first = document.querySelector("#facetBody a, #facetBody button");
            if (first) {
                first.focus();
            }
        } else if (event.target.closest("[data-facet-drawer-close]")) {
            event.preventDefault();
            setOpen(false);
        }
    });

    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && document.body.classList.contains("facet-drawer-open")) {
            setOpen(false);
        }
    });
})();
