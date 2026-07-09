# WW20'26 – CFE Staff Summary

## Executive Summary

This summary covers CFE staff updates for WW20'26, including Wi-Fi/BT product priorities, customer feedback, technical blockers, CPU roadmap projections (2026–2028), and feature enablement roles. Key highlights: prioritization of CHRE + Channel Sounding for BE211/BE213, critical DELL RFIm/BW320 issue, stable but declining ICPS SW quality, and multiple roadmap/strategy sessions focused on AI adoption and platform differentiation. Risks include memory constraints for HDT, ICPS quality decline, and platform-specific technical gaps. CPU roadmap features multiple launches and transitions through 2028, with clear delineation of feature and account owner responsibilities.

---

## Key Points

### Product/Technical Priorities
- CHRE + Channel Sounding prioritized over Dual MAC for BE211/BE213.
- HB remains on RZL; no schedule delay.
- Not enough memory for HDT implementation.
- WPA3 Enterprise fast transition required for Wi-Fi 6 certification; WPA3 on Windows now requires netadapter.
- New BIOS JSON available for sensing feature display.

### DELL/RFIm Issue
- DELL RFIm does not support BW320 by default; manual configuration required.
- Throughput drops observed with 320BW on 6G band.
- Reference docs: 816192, 640438.
- Example: Channel 69/BW320 fails (DDR freq stuck at 6385), Channel 85/BW20 works (DDR freq moves to 5985).

### Customer/Market Updates
- Googlebook launching as premium device brand (Acer, ASUS, Lenovo on Intel; Dell, HP on QCOM).
- Features: Gemini Intelligence, new contextual features.
- HP: Highest ASP ($7.5), high attach rates; Lenovo: lowest ASP ($4), rebate issues.
- Acer secured 100% Aspire volume via ICPS bundles.
- MSI lost share to AMD CPUs; fighting to retain Wi-Fi share.

### Customer Survey (Q1 2026)
- CSAT: 83.6% (up 1.5% from Q3'25), "Good" range.
- Top OEMs: MSI (97%), Panasonic Connect (96%), Microsoft (96%), Dell (95%), Acer (94%).
- ICPS SW Quality at 64% (−6%), Support at 70% (−4%)—both declining.
- NPS scores stable in "Favorable" range.

### Roadmap/Strategy Sessions
- Focus on AI PC with local NPU, hardware/software roadmap reviews, customer engagement.
- AI adoption: Code commit rates up, but review bottlenecks; prediction that only 50% of engineers needed in two years due to AI.
- Platform differentiation: More field demos, UX-based testing.

### CPU Product Roadmap (2026–2028)
- Planned launches: Granite Rapids, Halo, Arrow Lake Refresh, Nova Lake (DT & Mobile), Panther Lake, Wildcat Lake Refresh, Raptor Lake S/HX Next, Twin Lake, Razor Lake, Moon Lake.
- Nova Lake DT/Mobile production starts Q4’26/Q1’27; Panther Lake/Wildcat Lake refreshes in Q’27; Moon Lake 16W pulled in.
- Arrow Lake HX-R vs Nova Lake HX: Up to 24-core vs 28-core; improvements in MT, gaming, battery life, AI, BOM.
- Pin/package compatibility between Nova Lake HX and NHL.

### Feature Enablement Roles
- Feature Owner: Deep feature expertise, demo readiness.
- Account Owner: Customer support and real-world enablement.

### Miscellaneous
- "WiTS" noted without context.

---

## Decisions

- Prioritize CHRE + Channel Sounding on BE211/BE213 over Dual MAC.
- Customer must manually add BW320 support to RFIm configuration.
- Continue Wi-Fi 6 PTL through NVL/RZL timeline despite missing out on some MU.
- MSFT prefers Lossless audio; SCI prioritized over Lossless.

---

## Action Items

- **DELL RFIm Issue**: Customer to manually add BW320 support; refer to docs 816192 & 640438. *(Owner: DELL/Intel support)*
- **BIOS Update**: Teams to reference new BIOS JSON for sensing feature display.
- **ICPS Quality**: Focused attention needed to address declining ICPS SW quality/support. *(Owner: Engineering/Support teams)*
- **Acer**: Provide data on BT testing Neo. *(Owner: Izhak)*
- **Asus**: Provide data on Wi-Fi 6 comps vs GfP2. *(Owner: Unspecified)*
- **MSFT**: Set up SharePoint site for SW sharing; plan HDT enablement for 2027–28.
- **SCI Dongle Solution**: Offer to achieve 2KHz as interim solution.

---

## Risks/Blockers

- Not enough memory for HDT implementation.
- ICPS SW Quality and Support declining—risk to customer satisfaction.
- Vaio and Compal have lowest CSAT scores; risk of customer churn.
- DELL RFIm/BW320 issue may impact throughput if not addressed.
- Review bottlenecks slowing AI-driven code commits.
- Platform-specific technical gaps (e.g., WiFi Aware onboarding not supported on iOS/Android).

---

## Open Questions

- Why does CNVi save power? Engineering team unclear.
- Will Asus increase Wi-Fi attach/value?
- How to normalize CSAT scores for more accurate OEM comparisons?
- What is the timeline for deleting all rebates by the Lenovo contract in 2027?
- What is the plan for broader WiFi Aware onboarding support?