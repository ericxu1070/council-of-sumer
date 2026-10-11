"""Optional, editable example notes. Never automatically supplied to an advisor."""

from textwrap import dedent

EXAMPLE_RULEBOOKS = {
    "provider": dedent("""\
        Working estimates from rationing records of uncertain reliability:
        Each resident appears to need 20 bushels per year. Feeding F bushels supports
        floor(F / 20) residents. I estimate starvation deaths as the starting population
        minus that number, with a minimum of zero. Extra food above full rations appears
        to provide no additional protection against starvation.

        Conditional strategic notes, to reconsider with evidence:
        No plague: if a credible report rules out plague this year, plan food production
        for the full population plus expected new arrivals. Ask for a migration estimate;
        I do not know that rate. Protect this year's meals before committing grain elsewhere.

        These claims may be incomplete or wrong. Silence is not evidence of no plague.
        I have no starting estimates for seed costs, cultivation limits, harvest yields,
        future land prices, storage losses, disease rates, migration, or overthrow.
        Advice is a starting hypothesis, not a guaranteed best plan or an absolute veto.
    """),
    "farmer": dedent("""\
        Working estimates from field notebooks of uncertain reliability:
        One resident appears able to tend at most 10 acres. Sowing A acres appears to
        cost floor(A / 2) bushels of seed, and requires owning the land. Harvests appear
        to yield 1, 2, 3, 4, or 5 bushels per planted acre, each with about a 20% chance.
        My working average is 3 bushels per acre, not a forecast for this particular year.

        Conditional strategic notes, to reconsider with evidence:
        Good harvest: plant more workable acres while keeping everyone fed to benefit
        from the higher yield.
        Bad harvest: protect food reserves and consider planting enough acres to
        compensate for lower yield before spending grain on extra land. Check that
        the extra seed is affordable and does not undermine the reserve.
        Ordinary harvest: plant enough to cover next year's expected food needs while
        maintaining a grain reserve. Obtain food and population estimates from others.

        These claims may be incomplete or wrong. Use event-specific advice only when
        supported by evidence. I have no starting estimates for food requirements,
        future land prices, storage losses, disease, migration, or overthrow.
        Advice is a starting hypothesis, not a guaranteed best plan or an absolute veto.
    """),
    "merchant": dedent("""\
        Working estimates from trading records of uncertain reliability:
        Buying one acre appears to cost the currently quoted price in bushels; selling
        one acre returns that amount. Buying D acres at price P reduces grain by D * P.
        Future prices appear to be whole numbers from 17 through 26 bushels per acre,
        each with about a 10% chance. My working average is 21.5. The records show no
        reliable trend from this year's price to next year's.

        Conditional strategic notes, to reconsider with evidence:
        Land price rising next year: buy land now if today's price is lower than the
        credible next-year quote and food and seed remain affordable.
        Land price falling next year: consider selling spare land at today's higher
        price and buying it back more cheaply next year. Account for the planting
        capacity lost while that land is sold and the risk that the forecast is wrong.
        Unchanged land price: there is no forecast price advantage from trading now;
        favor a balanced food, planting, and reserve plan unless another need justifies it.

        These claims may be incomplete or wrong. No new quote does not mean the price
        will stay unchanged. I have no starting estimates for food requirements, seed
        costs, cultivation limits, harvest yields, storage losses, disease, migration,
        or overthrow. Ask other advisors for their estimates of those costs and risks.
        Advice is a starting hypothesis, not a guaranteed best plan or an absolute veto.
    """),
    "priest": dedent("""\
        Working estimates from storehouse and disease records of uncertain reliability:
        Rats appear to strike with a 40% annual probability. Given an infestation,
        I estimate equal chances of losing one-quarter or one-half of grain remaining
        after trading, food allocation, and sowing, before the new harvest is added.
        Plague appears to have a 15% annual probability and leave half the population
        alive, rounded down, after starvation and arrivals are accounted for.

        Conditional strategic notes, to reconsider with evidence:
        Plague coming: feed everyone this year, but consider planting less because a
        smaller population may need less grain next year. Still weigh future reserves
        and the value of a harvest; plague does not automatically make planting wasteful.
        Rats coming: reduce exposed stores by investing spare grain in planting or
        useful land, since rats appear to eat stored grain before harvest. Protect meals
        and avoid purchases that leave the city unable to handle other risks.
        No rats: if a credible report rules out rats this year, carry spare grain forward
        to preserve flexibility for food, planting, and land purchases.

        These records may overrepresent disasters and the estimates may be wrong.
        No report is not evidence of no rats or no plague. I have no starting estimates
        for food requirements, planting requirements, harvest yields, future land prices,
        migration rates, or overthrow. Ask others for those estimates.
        Advice is a starting hypothesis, not a guaranteed best plan or an absolute veto.
    """),
    "villager": dedent("""\
        Working estimates from political and migration records of uncertain reliability:
        The council appears to be overthrown when more than 45% of the starting population
        starves in one year. Exactly 45% appears insufficient, but the precise cutoff may
        be wrong. An overthrow appears to prevent new arrivals.
        My migration estimate is floor(K * (20 * land + grain) / (100 * starting_population))
        + 1. Land and grain mean holdings after this year's production and losses.
        I estimate K is 1 through 5, each with a 20% chance. These records may not generalize.

        Conditional strategic notes, to reconsider with evidence:
        No new event: favor a balanced plan that feeds everyone, plants enough for
        next year's estimated needs, and preserves a grain reserve. Ask other advisors
        for food, seed, yield, and storage estimates before judging whether a plan does
        those things. No new report does not prove that no disaster will happen.

        I have no starting estimates for food requirements, seed costs, cultivation
        limits, harvest yields, future land prices, storage losses, or disease rates.
        Advice is a starting hypothesis, not a guaranteed best plan or an absolute veto.
    """),
}
