# 2026-10-23 — Siri date commands (verbatim feedback)

## Request

> is there a straightforward way to add simple date commands to the siri trigger like "hey siri, play yesterday's flowcrate" or "hey siri, play the flowcrate from last wednesday" or "hey siri play flowcrate from october 15". i don't care if that's the exact wording UX or not but i'd rather not have to update the Siri action if possible. if it's possible, figure out everything you need to implement it autonomously with only a single turn of input/actions/clarity from me. also, before you work on all that, use flowcrate to play yesterday's flow state on my speakers anyway and learn from it.

## Answers to the one up-front question set

1. Siri setup: Add "Rewind Flow Crate" (Recommended) — one new shortcut that asks "Which day?"; existing "Play Flow Crate" unchanged.
2. If the named day has no post:
   > there was absolutely a post on 10/21. https://www.flowstate.fm/p/julia-gjertsen-c0c Dig into why you/Flow Crate didn't find it because that could be a serious bug. is it really missing tons of posts??
3. Deploy:
   > Do all of #1 and also update the website to mention the new feature (without drawing attention to it). (are you aware of the website or is that a separate project?)

   (#1 = commit to main, push to zizmax/FlowCrate, reinstall on the Pi, restart, test by playing a past day.)
4. Uncommitted Spotify 127.0.0.1 redirect changes in the main checkout:
   > I'm afraid to tell you what to do without knowing the history/context of the changes. Do more investigating on the what/why so I can more confidently decide. I'm worried because I want the github to be generic but the Pi to be config'd for my network and I can't remember why/when/what I would have changed it and not commit/push, but i know i had a lot of problems getting it working just right in my env, so i want to be really careful here
