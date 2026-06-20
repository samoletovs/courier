using './main.bicep'

// Suffix for globally-unique names. Change to your own random string.
param suffix = 'c0ur1er'

// Region for the Function App and supporting resources.
param location = 'northeurope'

// Recipient allowlist (open-relay guard). Read from env at deploy:
//   $env:COURIER_ALLOWED_RECIPIENTS = 'you@example.com'
// Set this to your inbox (or a domain) before deploying.
param allowedRecipients = readEnvironmentVariable('COURIER_ALLOWED_RECIPIENTS', 'CHANGE_ME@example.com')
