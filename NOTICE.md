# NOTICE

This repository, `A-stock-tail`, contains local application code and documentation for an A-share overnight observation workflow.

## Upstream Data Skill

The A-share data Skill content in `SKILL.md` is derived from the upstream project:

- Project: `a-stock-data`
- Upstream URL: https://github.com/simonlin1212/a-stock-data
- Author: Simon 林
- License: Apache License 2.0

The upstream project provides the A-share data-source capability, including market data, reports, signals, capital-flow data, news, announcements, and related helper code. Attribution to the upstream author and project must be preserved.

## Local Application Layer

The `overnight_quant/` directory is an example application layer for local research workflows, including:

- after-close observation
- tail-session dry-run scans
- pre-market and intraday checks
- manual position updates
- manual sell-plan reminders
- dashboard display

This local application layer is packaged with the data Skill to form the `A-stock-tail` workspace.

## Safety Boundary

This project does not automatically trade. It does not click brokerage software and does not call brokerage trading APIs. Reports and dashboard outputs are for manual research, observation, and review only.

## No Upstream Endorsement

References to `a-stock-data`, Simon 林, or the upstream repository do not imply endorsement of this repository by the upstream author.

