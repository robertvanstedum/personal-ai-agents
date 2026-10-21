"""MC delegation dispatcher: the counterpart of core/mc_delegation_runner. The portal's jobs
manager starts delegated work through ``relay.DelegationRelay``; the Mac runner claims it through
``store.DispatcherStore`` (the runner protocol); results come back as the job's result note."""
