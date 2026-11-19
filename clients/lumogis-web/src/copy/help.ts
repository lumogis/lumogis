// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Lumogis
//
// Every page purpose line and (i) help text in one place (LUM-993).
//
// Style (Thomas, 17 Nov 2026, "C, short like A"): say what it does, then why or
// what follows, in at most two short sentences. Plain words: no ids, enum values,
// file paths or shell commands. tests/design/helpCopy.test.ts enforces the shape.
//
// Keys: the top-level key is the page (its Settings slug in camelCase, or `chat`,
// `scope`, `errors`, `devTools`). `page` is the one-line purpose under the title,
// `about` the page's (i) text; any other key is the (i) for a field or action.

export const help = {
  profile: {
    page: "Your sign-in details for this Lumogis.",
    about: "Everyone in the household signs in separately. What you add stays yours unless you share it.",
    role: "Admins can invite people and change household settings. Members can use everything else.",
    password: "Forgot it? Ask a household admin to set a new one for you.",
  },
  settings: {
    page: "Everything you can set up in Lumogis, grouped by what it is for.",
  },
  appearance: {
    page: "How Lumogis looks on this device.",
    about: "The choice is kept in this browser only, so each device can look different.",
  },
  notifications: {
    page: "Choose what Lumogis tells you about, and where.",
    about: "Notifications go to this browser or to a phone app. Turn on the ones that matter and Lumogis stays quiet otherwise.",
  },
  email: {
    page: "Connect your mailbox so Lumogis can sort your mail and tell you what needs you.",
    about: "Lumogis reads your mail to sort it and spot what needs you. It sends mail only if you turn sending on.",
  },
  emailLabels: {
    page: "The labels Lumogis uses to sort your mail, and the rules behind them.",
    about: "Lumogis sorts your mail into these labels. Edit them to match how you think about your mail.",
  },
  whatMatters: {
    page: "Tell Lumogis which people and topics matter most to you.",
    about: "Lumogis uses these to decide what to show you first.",
  },
  sources: {
    page: "Folders, document archives, feeds and web pages Lumogis reads for you.",
    about: "Lumogis checks each one on a schedule and adds what is new to your library.",
  },
  connections: {
    page: "Accounts and services Lumogis can use for you, such as a document archive.",
    about: "Keys you save here are stored encrypted on this computer. Remove one and Lumogis stops using that service.",
  },
  aiServices: {
    page: "Online AI services Lumogis may use, and the keys for them.",
    about: "Add a key only for a service you want to use. Online AI is off while privacy mode keeps everything local.",
  },
  connectedApps: {
    page: "Keys that let other AI apps, such as Claude Desktop, use your Lumogis memory. Remove one if a device is lost.",
    about: "An app with a key uses Lumogis as you. Make one key per app, so you can remove one without the others.",
  },
  permissions: {
    page: "Decide what Lumogis may do on its own and what it asks you first.",
    about: "Ask means you approve each action. Do means Lumogis goes ahead and records it in Activity.",
  },
  privacy: {
    page: "Choose whether Lumogis may use online AI. Local only keeps everything on this computer.",
    about: "Online AI can be faster or better at some tasks. With local only, nothing you ask leaves this computer.",
  },
  shared: {
    page: "Everything you have shared with the rest of the household.",
    about: "Stop sharing an item here and it becomes private again.",
  },
  wontLearn: {
    page: "Names you told Lumogis to forget, so it does not learn them again.",
    about: "Remove a name to let Lumogis learn it again from your documents.",
  },
  activity: {
    page: "What Lumogis did for you and when.",
    about: "Every action Lumogis takes is listed here, including ones it was not allowed to do.",
  },
  export: {
    page: "Download a copy of your documents, notes and settings.",
    about: "The download holds only your own data. Keep it somewhere safe.",
  },
  members: {
    page: "Everyone in the household, and invitations waiting to be used.",
    about: "Admins can invite people, reset passwords and change household settings. Members use everything else.",
  },
  health: {
    page: "Whether everything is running, the last backup, and disk space.",
    about: "Check here first when something seems wrong. Each line says which part needs attention.",
  },
  homeAddress: {
    page: "A secure web address for Lumogis on your home network.",
    about: "Phones need a secure address for voice input, notifications and installing the app.",
  },
  systemMail: {
    page: "The mailbox Lumogis uses to send invitations and password resets.",
    about: "Without it, invitations still work as links you copy and send yourself.",
  },
  searchRanking: {
    page: "Fine-tune how Lumogis ranks search results.",
    about: "The defaults suit most households. Change these only if search keeps missing what you expect.",
  },
  knowledgeGraph: {
    page: "The map of people, places and topics Lumogis builds from your documents.",
    about: "It needs more memory than the rest of Lumogis. Turn it off and your documents and search still work.",
  },
  diagnostics: {
    page: "Technical checks for troubleshooting Lumogis.",
    about: "Useful when someone is helping you fix a problem. Nothing here changes your data.",
  },
  tools: {
    page: "The tools Lumogis and connected services can use, and what each may touch.",
    about: "A list for troubleshooting. Whether a tool may act is set in What Lumogis may do.",
  },
  featureFlags: {
    page: "Switches for features that are still being tried out.",
    about: "Leave these as they are unless someone helping you asks you to change one.",
  },
  safety: {
    page: "Try out what the safety checks would allow or block.",
    about: "Test runs here change nothing. They show how Lumogis would treat a real request.",
  },
  chat: {
    history: "Open chats stay in this tab. Closed chats are kept as summaries in History.",
    historyAbout: "Lumogis keeps a short summary of each closed chat, not the full text. Those summaries are what History shows and what Lumogis can recall later.",
  },
  scope: {
    about: "Household shows everyone's. Only admins see this switch.",
  },
  devTools: {
    about: "Shows pages for troubleshooting Lumogis. Nothing here is needed day to day.",
  },
  errors: {
    persistent: "If this keeps happening, ask whoever looks after Lumogis to check its Health page.",
  },
} as const;

type Help = typeof help;
export type HelpPage = keyof Help;
export type HelpKey = { [P in HelpPage]: `${P}.${Extract<keyof Help[P], string>}` }[HelpPage];
