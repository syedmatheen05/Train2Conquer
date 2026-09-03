"use strict";

/* =========================================================
   TRAIN2CONQUER WORKOUT ENGINE
   =========================================================
   FEATURES
   ---------------------------------------------------------
   ✓ 10 second Get Ready countdown
   ✓ Timed exercises
   ✓ PAUSE / RESUME for timed exercises
   ✓ Accurate timer using timestamps
   ✓ Mobile-safe timer
   ✓ Landscape / portrait video support
   ✓ Previous button
   ✓ Done button
   ✓ Finish button
   ✓ Rest timer
   ✓ Skip Rest
   ✓ Speech countdown
   ✓ Prevents duplicate timers
   ✓ Handles mobile tab switching
========================================================= */


/* =========================================================
   WORKOUT DATA
========================================================= */

let currentWorkout = 0;

let currentScreen = "ready";

/*
 * Exercise timer
 */
let exerciseTime = 0;
let exerciseEndAt = null;
let exerciseInterval = null;

/*
 * Rest timer
 */
let restTime = 0;
let restEndAt = null;
let restInterval = null;

/*
 * Get ready timer
 */
let readyCountdown = 10;
let readyEndAt = null;
let readyInterval = null;

/*
 * Pause state
 */
let exercisePaused = false;
let exercisePausedRemaining = 0;

/*
 * Prevent multiple submissions
 */
let isFinishing = false;


/* =========================================================
   ELEMENTS
========================================================= */

const video =
    document.getElementById("workout-video");

const videoSource =
    document.getElementById("video-source");

const workoutNumber =
    document.getElementById("workout-number");

const exerciseName =
    document.getElementById("exercise-name");

const reps =
    document.getElementById("reps");

const previousButton =
    document.getElementById("previous-button");

const nextButton =
    document.getElementById("next-button");

const navigationButtons =
    document.getElementById("navigation-buttons");

const workoutCard =
    document.querySelector(".workout-card");

const restScreen =
    document.getElementById("rest-screen");

const restTitle =
    document.getElementById("rest-title");

const restMessage =
    document.getElementById("rest-message");

const restTimer =
    document.getElementById("rest-timer");

const nextExercise =
    document.getElementById("next-exercise");

const skipRest =
    document.getElementById("skip-rest");

const addRestTimeButton =
    document.getElementById("add-rest-time");

const completeWorkoutForm =
    document.getElementById("completeWorkoutForm");

/*
 * New pause button.
 */
const pauseExerciseButton =
    document.getElementById("pause-exercise");


/* =========================================================
   BASIC HELPERS
========================================================= */

function safeNumber(value, fallback = 0) {

    const number = Number(value);

    if (
        !Number.isFinite(number) ||
        number < 0
    ) {
        return fallback;
    }

    return number;
}


function formatSeconds(seconds) {

    const value =
        Math.max(
            0,
            Math.ceil(
                Number(seconds) || 0
            )
        );

    return `${value} seconds`;
}


/* =========================================================
   SPEECH
========================================================= */

function speak(text) {

    if (
        !("speechSynthesis" in window)
    ) {
        return;
    }

    window.speechSynthesis.cancel();

    const speech =
        new SpeechSynthesisUtterance(
            String(text)
        );

    speech.rate = 0.9;
    speech.pitch = 1;
    speech.volume = 1;

    window.speechSynthesis.speak(
        speech
    );
}


function speakCountdown(number) {

    if (
        !("speechSynthesis" in window)
    ) {
        return;
    }

    window.speechSynthesis.cancel();

    const speech =
        new SpeechSynthesisUtterance(
            String(number)
        );

    speech.rate = 1.2;
    speech.pitch = 1;
    speech.volume = 1;

    window.speechSynthesis.speak(
        speech
    );
}


/* =========================================================
   TIMER CLEANUP
========================================================= */

function clearExerciseTimer() {

    if (
        exerciseInterval !== null
    ) {

        clearInterval(
            exerciseInterval
        );

        exerciseInterval = null;
    }

    exerciseEndAt = null;
}


function clearRestTimer() {

    if (
        restInterval !== null
    ) {

        clearInterval(
            restInterval
        );

        restInterval = null;
    }

    restEndAt = null;
}


function clearReadyTimer() {

    if (
        readyInterval !== null
    ) {

        clearInterval(
            readyInterval
        );

        readyInterval = null;
    }

    readyEndAt = null;
}


function stopAllTimers() {

    clearExerciseTimer();
    clearRestTimer();
    clearReadyTimer();
}


function stopEverything() {

    stopAllTimers();

    if (
        "speechSynthesis" in window
    ) {
        window.speechSynthesis.cancel();
    }

    if (video) {
        video.pause();
    }

    exercisePaused = false;
    exercisePausedRemaining = 0;

    updatePauseButton();
}


/* =========================================================
   SCREEN CONTROL
========================================================= */

function showWorkoutScreen() {

    if (workoutCard) {

        workoutCard.classList.remove(
            "d-none"
        );
    }

    if (navigationButtons) {

        navigationButtons.classList.remove(
            "d-none"
        );
    }

    if (restScreen) {

        restScreen.classList.add(
            "d-none"
        );
    }
}


function showRestScreen() {

    if (workoutCard) {

        workoutCard.classList.add(
            "d-none"
        );
    }

    if (navigationButtons) {

        navigationButtons.classList.add(
            "d-none"
        );
    }

    if (restScreen) {

        restScreen.classList.remove(
            "d-none"
        );
    }
}


/* =========================================================
   REST-ONLY CONTROLS (Skip Rest / Add Rest)
   ---------------------------------------------------------
   The Get Ready countdown reuses the same #rest-screen markup
   as the real rest screen (same styling/layout). Skip Rest and
   Add Rest only make sense once currentScreen === "rest" — if
   left visible during Get Ready they LOOK clickable but silently
   no-op, which is the "Skip Rest doesn't work the first time"
   bug. Keep them hidden until a real rest period starts.
========================================================= */

function hideRestOnlyControls() {

    if (skipRest) {

        skipRest.classList.add(
            "d-none"
        );
    }

    if (addRestTimeButton) {

        addRestTimeButton.classList.add(
            "d-none"
        );
    }
}


function showRestOnlyControls() {

    if (skipRest) {

        skipRest.classList.remove(
            "d-none"
        );
    }

    if (addRestTimeButton) {

        addRestTimeButton.classList.remove(
            "d-none"
        );
    }
}


/* =========================================================
   PAUSE BUTTON
========================================================= */

function updatePauseButton() {

    if (!pauseExerciseButton) {
        return;
    }

    /*
     * Pause button is visible only for
     * timed exercises.
     */

    const workout =
        workouts[currentWorkout];

    const isTimed =
        workout &&
        safeNumber(
            workout.seconds,
            0
        ) > 0;

    if (
        currentScreen !== "workout" ||
        !isTimed ||
        exerciseTime <= 0
    ) {

        pauseExerciseButton.classList.add(
            "d-none"
        );

        return;
    }

    pauseExerciseButton.classList.remove(
        "d-none"
    );


    if (exercisePaused) {

        pauseExerciseButton.textContent =
            "▶ RESUME";

        pauseExerciseButton.setAttribute(
            "aria-label",
            "Resume workout"
        );

        pauseExerciseButton.classList.add(
            "is-paused"
        );

    } else {

        pauseExerciseButton.textContent =
            "Ⅱ PAUSE";

        pauseExerciseButton.setAttribute(
            "aria-label",
            "Pause workout"
        );

        pauseExerciseButton.classList.remove(
            "is-paused"
        );
    }
}


/* =========================================================
   EXERCISE TIMER UI
========================================================= */

function updateExerciseTimerUI(
    seconds
) {

    const value =
        Math.max(
            0,
            Math.ceil(
                Number(seconds) || 0
            )
        );

    exerciseTime = value;

    if (reps) {

        reps.textContent =
            `${value} SEC`;
    }

    updatePauseButton();
}


/* =========================================================
   REST TIMER UI
========================================================= */

function updateRestTimerUI(
    seconds
) {

    const value =
        Math.max(
            0,
            Math.ceil(
                Number(seconds) || 0
            )
        );

    restTime = value;

    if (restTimer) {

        restTimer.textContent =
            formatSeconds(value);
    }
}


/* =========================================================
   VIDEO
========================================================= */

function stopVideo() {

    if (!video) {
        return;
    }

    video.pause();

    try {

        video.currentTime = 0;

    } catch (error) {

        console.log(
            "Could not reset video position."
        );
    }
}


function loadExerciseVideo(
    videoUrl
) {

    if (
        !video ||
        !videoSource
    ) {
        return;
    }

    const wrapper =
        video.closest(
            ".t2c-video-wrapper"
        );


    /*
     * No video.
     */

    if (!videoUrl) {

        stopVideo();

        videoSource.removeAttribute(
            "src"
        );

        video.removeAttribute(
            "src"
        );

        if (wrapper) {

            wrapper.style.display =
                "none";
        }

        return;
    }


    /*
     * Show video wrapper.
     */

    if (wrapper) {

        wrapper.style.display =
            "block";
    }


    /*
     * Stop previous video.
     */

    video.pause();


    /*
     * Load new source.
     */

    videoSource.src =
        String(videoUrl);

    video.load();


    /*
     * Try autoplay.
     * Muted + playsinline allows autoplay
     * on most mobile browsers.
     */

    const playPromise =
        video.play();

    if (
        playPromise &&
        typeof playPromise.catch ===
            "function"
    ) {

        playPromise.catch(
            function(error) {

                console.log(
                    "Autoplay blocked:",
                    error
                );
            }
        );
    }
}


/* =========================================================
   LOAD WORKOUT
========================================================= */

function loadWorkout(index) {

    const workout =
        workouts[index];

    if (!workout) {
        return;
    }


    /*
     * Stop previous exercise.
     */

    stopAllTimers();

    if (
        "speechSynthesis" in window
    ) {

        window.speechSynthesis.cancel();
    }


    /*
     * State.
     */

    currentWorkout =
        index;

    currentScreen =
        "workout";

    exercisePaused =
        false;

    exercisePausedRemaining =
        0;


    /*
     * Workout counter.
     */

    if (workoutNumber) {

        workoutNumber.textContent =
            `${index + 1}/${workouts.length}`;
    }


    /*
     * Exercise name.
     */

    if (exerciseName) {

        exerciseName.textContent =
            workout.exercise ||
            "Exercise";
    }


    /*
     * Check whether exercise is timed.
     */

    const seconds =
        safeNumber(
            workout.seconds,
            0
        );


    if (seconds > 0) {

        updateExerciseTimerUI(
            seconds
        );

    } else {

        if (reps) {

            reps.textContent =
                workout.reps || "";
        }

        updatePauseButton();
    }


    /*
     * Video.
     */

    loadExerciseVideo(
        workout.video
    );


    /*
     * Previous.
     */

    if (previousButton) {

        previousButton.disabled =
            index === 0;
    }


    /*
     * Done / Finish.
     */

    if (nextButton) {

        if (
            index ===
            workouts.length - 1
        ) {

            nextButton.textContent =
                "FINISH ✓";

            nextButton.classList.add(
                "btn-success"
            );

        } else {

            nextButton.textContent =
                "DONE →";

            nextButton.classList.remove(
                "btn-success"
            );
        }
    }


    /*
     * Show workout.
     */

    showWorkoutScreen();


    /*
     * Start timer only if exercise
     * is time-based.
     */

    if (seconds > 0) {

        startExerciseTimer();

    } else {

        updatePauseButton();
    }
}


/* =========================================================
   START EXERCISE TIMER
========================================================= */

function startExerciseTimer() {

    clearExerciseTimer();

    const workout =
        workouts[currentWorkout];

    if (!workout) {
        return;
    }


    const seconds =
        safeNumber(
            workout.seconds,
            0
        );


    if (seconds <= 0) {

        updatePauseButton();

        return;
    }


    exercisePaused =
        false;

    exercisePausedRemaining =
        0;


    exerciseTime =
        seconds;


    exerciseEndAt =
        Date.now() +
        seconds * 1000;


    updateExerciseTimerUI(
        seconds
    );


    exerciseInterval =
        setInterval(
            updateExerciseTimer,
            200
        );


    updateExerciseTimer();
}


/* =========================================================
   UPDATE EXERCISE TIMER
========================================================= */

function updateExerciseTimer() {

    if (
        currentScreen !==
        "workout"
    ) {

        clearExerciseTimer();

        return;
    }


    /*
     * If paused, timer must NEVER
     * continue.
     */

    if (exercisePaused) {
        return;
    }


    if (
        exerciseEndAt === null
    ) {

        clearExerciseTimer();

        return;
    }


    const remaining =
        Math.max(
            0,
            (
                exerciseEndAt -
                Date.now()
            ) / 1000
        );


    const previousSecond =
        exerciseTime;


    updateExerciseTimerUI(
        remaining
    );


    const currentSecond =
        exerciseTime;


    /*
     * Countdown voice.
     */

    if (
        previousSecond !==
            currentSecond &&
        currentSecond <= 3 &&
        currentSecond > 0
    ) {

        speakCountdown(
            currentSecond
        );
    }


    /*
     * Exercise finished.
     */

    if (
        remaining <= 0
    ) {

        finishExercise();
    }
}


/* =========================================================
   PAUSE EXERCISE
========================================================= */

function pauseExercise() {

    if (
        currentScreen !==
        "workout"
    ) {
        return;
    }


    if (exercisePaused) {
        return;
    }


    if (
        exerciseEndAt === null ||
        exerciseTime <= 0
    ) {
        return;
    }


    /*
     * Calculate EXACT remaining time
     * before pausing.
     */

    exercisePausedRemaining =
        Math.max(
            0,
            (
                exerciseEndAt -
                Date.now()
            ) / 1000
        );


    exerciseTime =
        Math.ceil(
            exercisePausedRemaining
        );


    /*
     * Mark paused.
     */

    exercisePaused =
        true;


    /*
     * Stop interval.
     */

    if (
        exerciseInterval !== null
    ) {

        clearInterval(
            exerciseInterval
        );

        exerciseInterval = null;
    }


    /*
     * Remove deadline.
     */

    exerciseEndAt =
        null;


    /*
     * Pause video.
     */

    if (video) {
        video.pause();
    }


    /*
     * Stop speech.
     */

    if (
        "speechSynthesis" in window
    ) {

        window.speechSynthesis.cancel();
    }


    updateExerciseTimerUI(
        exercisePausedRemaining
    );

    updatePauseButton();
}


/* =========================================================
   RESUME EXERCISE
========================================================= */

function resumeExercise() {

    if (
        currentScreen !==
        "workout"
    ) {
        return;
    }


    if (!exercisePaused) {
        return;
    }


    if (
        exercisePausedRemaining <= 0
    ) {

        exercisePaused =
            false;

        finishExercise();

        return;
    }


    /*
     * Restart deadline using ONLY
     * the remaining paused time.
     */

    exerciseEndAt =
        Date.now() +
        exercisePausedRemaining *
            1000;


    exercisePaused =
        false;


    /*
     * Restart timer.
     */

    exerciseInterval =
        setInterval(
            updateExerciseTimer,
            200
        );


    /*
     * Resume video.
     */

    if (video) {

        const playPromise =
            video.play();

        if (
            playPromise &&
            typeof playPromise.catch ===
                "function"
        ) {

            playPromise.catch(
                function(error) {

                    console.log(
                        "Video resume blocked:",
                        error
                    );
                }
            );
        }
    }


    updateExerciseTimer();

    updatePauseButton();
}


/* =========================================================
   PAUSE BUTTON CLICK
========================================================= */

if (pauseExerciseButton) {

    pauseExerciseButton.addEventListener(
        "click",
        function(event) {

            event.preventDefault();

            if (
                exercisePaused
            ) {

                resumeExercise();

            } else {

                pauseExercise();
            }
        }
    );
}


/* =========================================================
   FINISH EXERCISE
========================================================= */

function finishExercise() {

    clearExerciseTimer();

    exercisePaused =
        false;

    exercisePausedRemaining =
        0;

    updateExerciseTimerUI(
        0
    );


    if (video) {
        video.pause();
    }


    updatePauseButton();


    /*
     * Last exercise.
     */

    if (
        currentWorkout ===
        workouts.length - 1
    ) {

        if (reps) {

            reps.textContent =
                "DONE ✓";
        }

        return;
    }


    /*
     * Automatically enter rest.
     */

    startRest();
}


/* =========================================================
   START REST
========================================================= */

function startRest() {

    stopAllTimers();

    if (
        "speechSynthesis" in window
    ) {

        window.speechSynthesis.cancel();
    }


    currentScreen =
        "rest";


    if (restTitle) {

        restTitle.textContent =
            "REST";
    }


    if (restMessage) {

        restMessage.textContent =
            "Take a short break before your next exercise.";
    }


    const nextIndex =
        currentWorkout + 1;


    if (
        nextExercise
    ) {

        if (
            nextIndex <
            workouts.length
        ) {

            nextExercise.textContent =
                `Next movement: ${
                    workouts[
                        nextIndex
                    ].exercise
                }`;

        } else {

            nextExercise.textContent =
                "Your workout is almost complete.";
        }
    }


    /*
     * Rest duration.
     */

    restTime =
        safeNumber(
            workouts[
                currentWorkout
            ].rest,
            0
        );


    showRestScreen();


    /*
     * This is a genuine rest period, so
     * Skip Rest / Add Rest are usable now.
     */

    showRestOnlyControls();


    updateRestTimerUI(
        restTime
    );


    speak(
        "Take a rest"
    );


    /*
     * No rest.
     */

    if (
        restTime <= 0
    ) {

        finishRest();

        return;
    }


    restEndAt =
        Date.now() +
        restTime * 1000;


    restInterval =
        setInterval(
            updateRestTimer,
            200
        );


    updateRestTimer();
}


/* =========================================================
   UPDATE REST TIMER
========================================================= */

function updateRestTimer() {

    if (
        currentScreen !==
        "rest"
    ) {

        clearRestTimer();

        return;
    }


    if (
        restEndAt === null
    ) {

        clearRestTimer();

        return;
    }


    const remaining =
        Math.max(
            0,
            (
                restEndAt -
                Date.now()
            ) / 1000
        );


    const previousSecond =
        restTime;


    updateRestTimerUI(
        remaining
    );


    const currentSecond =
        restTime;


    /*
     * Announce next exercise
     * at 7 seconds.
     */

    if (
        previousSecond !==
            currentSecond &&
        currentSecond === 7 &&
        currentWorkout + 1 <
            workouts.length
    ) {

        speak(
            `Next exercise is ${
                workouts[
                    currentWorkout + 1
                ].exercise
            }`
        );
    }


    /*
     * Countdown.
     */

    if (
        previousSecond !==
            currentSecond &&
        currentSecond <= 3 &&
        currentSecond > 0
    ) {

        speakCountdown(
            currentSecond
        );
    }


    /*
     * Rest complete.
     */

    if (
        remaining <= 0
    ) {

        finishRest();
    }
}


/* =========================================================
   FINISH REST
========================================================= */

function finishRest() {

    clearRestTimer();

    if (
        "speechSynthesis" in window
    ) {

        window.speechSynthesis.cancel();
    }


    const nextIndex =
        currentWorkout + 1;


    if (
        nextIndex >=
        workouts.length
    ) {

        return;
    }


    currentWorkout =
        nextIndex;


    loadWorkout(
        nextIndex
    );


    /*
     * Announce exercise.
     */

    setTimeout(
        function() {

            if (
                currentScreen ===
                    "workout" &&
                workouts[
                    currentWorkout
                ]
            ) {

                speak(
                    workouts[
                        currentWorkout
                    ].exercise
                );
            }

        },
        300
    );
}


/* =========================================================
   DONE / FINISH BUTTON
========================================================= */

if (nextButton) {

    nextButton.addEventListener(
        "click",
        function(event) {

            event.preventDefault();


            if (isFinishing) {
                return;
            }


            /*
             * Last workout.
             */

            if (
                currentWorkout ===
                workouts.length - 1
            ) {

                isFinishing =
                    true;

                stopEverything();

                currentScreen =
                    "finished";


                if (
                    completeWorkoutForm
                ) {

                    /*
                     * Show your existing loader.
                     */

                    if (
                        window.T2CLoader
                    ) {

                        window.T2CLoader.show(
                            "Saving your workout..."
                        );
                    }


                    nextButton.disabled =
                        true;

                    nextButton.textContent =
                        "SAVING...";


                    /*
                     * Native Flask form submission.
                     */

                    completeWorkoutForm.submit();

                } else {

                    console.error(
                        "completeWorkoutForm not found."
                    );

                    isFinishing =
                        false;
                }

                return;
            }


            /*
             * Normal Done.
             */

            if (video) {
                video.pause();
            }


            startRest();
        }
    );
}


/* =========================================================
   PREVIOUS BUTTON
========================================================= */

if (previousButton) {

    previousButton.addEventListener(
        "click",
        function(event) {

            event.preventDefault();


            if (
                currentWorkout <= 0
            ) {

                return;
            }


            stopEverything();


            currentWorkout--;


            loadWorkout(
                currentWorkout
            );


            setTimeout(
                function() {

                    if (
                        currentScreen ===
                            "workout" &&
                        workouts[
                            currentWorkout
                        ]
                    ) {

                        speak(
                            workouts[
                                currentWorkout
                            ].exercise
                        );
                    }

                },
                300
            );
        }
    );
}


/* =========================================================
   SKIP REST
========================================================= */

if (skipRest) {

    skipRest.addEventListener(
        "click",
        function(event) {

            event.preventDefault();


            if (
                currentScreen !==
                "rest"
            ) {

                return;
            }


            clearRestTimer();


            if (
                "speechSynthesis" in window
            ) {

                window.speechSynthesis.cancel();
            }


            finishRest();
        }
    );
}


/* =========================================================
   ADD REST TIME (+20 SEC)
   ---------------------------------------------------------
   Timestamp-based: extends restEndAt directly rather than
   restarting the timer, so it works the first time, never
   resets progress, never spawns a second interval, and stacks
   correctly on repeated clicks.
========================================================= */

if (addRestTimeButton) {

    addRestTimeButton.addEventListener(
        "click",
        function(event) {

            event.preventDefault();


            if (
                currentScreen !==
                "rest"
            ) {

                return;
            }


            /*
             * If the rest timer already hit zero
             * (finishRest is about to fire / has fired),
             * there's nothing to extend.
             */

            if (
                restEndAt === null
            ) {

                return;
            }


            restEndAt =
                restEndAt +
                20 * 1000;


            /*
             * Reflect the new remaining time immediately;
             * the existing interval keeps ticking against
             * the updated deadline, so no duplicate timer
             * is ever created.
             */

            updateRestTimer();
        }
    );
}


/* =========================================================
   GET READY
========================================================= */

function updateReadyTimerUI(
    seconds
) {

    const value =
        Math.max(
            0,
            Math.ceil(
                Number(seconds) || 0
            )
        );

    readyCountdown =
        value;

    if (restTimer) {

        restTimer.textContent =
            formatSeconds(value);
    }
}


function startWorkoutCountdown() {

    if (
        !Array.isArray(workouts) ||
        workouts.length === 0
    ) {

        console.warn(
            "Train2Conquer: no workouts supplied."
        );

        return;
    }


    stopEverything();


    currentScreen =
        "ready";

    currentWorkout =
        0;


    /*
     * Counter.
     */

    if (workoutNumber) {

        workoutNumber.textContent =
            `0/${workouts.length}`;
    }


    /*
     * Show ready screen.
     */

    if (workoutCard) {

        workoutCard.classList.add(
            "d-none"
        );
    }


    if (navigationButtons) {

        navigationButtons.classList.add(
            "d-none"
        );
    }


    if (restScreen) {

        restScreen.classList.remove(
            "d-none"
        );
    }


    /*
     * Skip Rest / Add Rest belong to the real
     * rest screen only, not Get Ready.
     */

    hideRestOnlyControls();


    if (restTitle) {

        restTitle.textContent =
            "GET READY";
    }


    if (restMessage) {

        restMessage.textContent =
            "";
    }


    if (nextExercise) {

        nextExercise.textContent =
            `Your first exercise is ${
                workouts[0].exercise
            }`;
    }


    readyCountdown =
        10;


    updateReadyTimerUI(
        readyCountdown
    );


    speak(
        "Get ready"
    );


    readyEndAt =
        Date.now() +
        10 * 1000;


    readyInterval =
        setInterval(
            updateReadyTimer,
            200
        );


    updateReadyTimer();
}


/* =========================================================
   UPDATE GET READY
========================================================= */

function updateReadyTimer() {

    if (
        currentScreen !==
        "ready"
    ) {

        clearReadyTimer();

        return;
    }


    if (
        readyEndAt === null
    ) {

        clearReadyTimer();

        return;
    }


    const remaining =
        Math.max(
            0,
            (
                readyEndAt -
                Date.now()
            ) / 1000
        );


    const previousSecond =
        readyCountdown;


    updateReadyTimerUI(
        remaining
    );


    const currentSecond =
        readyCountdown;


    /*
     * 3 - 2 - 1 voice.
     */

    if (
        previousSecond !==
            currentSecond &&
        currentSecond <= 3 &&
        currentSecond > 0
    ) {

        speakCountdown(
            currentSecond
        );
    }


    /*
     * Countdown complete.
     */

    if (
        remaining <= 0
    ) {

        clearReadyTimer();


        if (
            "speechSynthesis" in window
        ) {

            window.speechSynthesis.cancel();
        }


        loadWorkout(0);


        setTimeout(
            function() {

                if (
                    currentScreen ===
                        "workout" &&
                    workouts[0]
                ) {

                    speak(
                        workouts[0].exercise
                    );
                }

            },
            300
        );
    }
}


/* =========================================================
   MOBILE VISIBILITY RECOVERY
========================================================= */

document.addEventListener(
    "visibilitychange",
    function() {

        if (
            document.visibilityState !==
            "visible"
        ) {

            /*
             * DO NOT pause the workout automatically.
             * The timestamp system will keep the timer
             * accurate when the browser returns.
             */

            return;
        }


        if (
            currentScreen ===
            "workout"
        ) {

            /*
             * If manually paused, remain paused.
             */

            if (!exercisePaused) {

                updateExerciseTimer();
            }

        } else if (
            currentScreen ===
            "rest"
        ) {

            updateRestTimer();

        } else if (
            currentScreen ===
            "ready"
        ) {

            updateReadyTimer();
        }
    }
);


/* =========================================================
   VIDEO ERROR HANDLING
========================================================= */

if (video) {

    video.addEventListener(
        "error",
        function() {

            console.warn(
                "Workout video could not be loaded:",
                video.currentSrc || ""
            );
        }
    );


    /*
     * Keep video inline on mobile.
     */

    video.setAttribute(
        "playsinline",
        ""
    );

    video.setAttribute(
        "webkit-playsinline",
        ""
    );

    video.muted = true;
}


/* =========================================================
   PREVENT ACCIDENTAL DOUBLE SUBMIT
========================================================= */

if (completeWorkoutForm) {

    completeWorkoutForm.addEventListener(
        "submit",
        function() {

            if (isFinishing) {
                return;
            }

            isFinishing =
                true;
        }
    );
}


/* =========================================================
   START APP
========================================================= */

if (
    Array.isArray(workouts) &&
    workouts.length > 0
) {

    startWorkoutCountdown();

} else {

    console.warn(
        "Train2Conquer: workouts array is empty."
    );
}