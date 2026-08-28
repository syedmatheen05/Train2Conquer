
/* =========================================================
   STATE
========================================================= */

let currentWorkout = 0;

let exerciseTime = 0;
let restTime = 0;
let readyCountdown = 10;

let exerciseInterval = null;
let restInterval = null;
let readyInterval = null;

let currentScreen = "ready";


/* =========================================================
   ELEMENTS
========================================================= */

const video = document.getElementById("workout-video");
const videoSource = document.getElementById("video-source");

const workoutNumber = document.getElementById("workout-number");

const restTitle = document.getElementById("rest-title");
const restMessage = document.getElementById("rest-message");

const exerciseName = document.getElementById("exercise-name");
const reps = document.getElementById("reps");

const previousButton = document.getElementById("previous-button");
const nextButton = document.getElementById("next-button");

const navigationButtons =
    document.getElementById("navigation-buttons");

const restScreen =
    document.getElementById("rest-screen");

const restTimer =
    document.getElementById("rest-timer");

const nextExercise =
    document.getElementById("next-exercise");

const skipRest =
    document.getElementById("skip-rest");

const completeWorkoutForm =
    document.getElementById("completeWorkoutForm");

const workoutCard =
    document.querySelector(".workout-card");


/* =========================================================
   SPEECH
========================================================= */

function speak(text) {

    window.speechSynthesis.cancel();

    const speech = new SpeechSynthesisUtterance(text);

    speech.rate = 0.9;
    speech.pitch = 1;
    speech.volume = 1;

    window.speechSynthesis.speak(speech);
}


function speakCountdown(number) {

    window.speechSynthesis.cancel();

    const speech =
        new SpeechSynthesisUtterance(String(number));

    speech.rate = 1.2;
    speech.pitch = 1;
    speech.volume = 1;

    window.speechSynthesis.speak(speech);
}


/* =========================================================
   STOP ALL TIMERS
========================================================= */

function stopAllTimers() {

    if (exerciseInterval !== null) {

        clearInterval(exerciseInterval);
        exerciseInterval = null;
    }

    if (restInterval !== null) {

        clearInterval(restInterval);
        restInterval = null;
    }

    if (readyInterval !== null) {

        clearInterval(readyInterval);
        readyInterval = null;
    }
}


/* =========================================================
   STOP EVERYTHING
========================================================= */

function stopEverything() {

    stopAllTimers();

    window.speechSynthesis.cancel();

    if (video) {
        video.pause();
    }
}


/* =========================================================
   SHOW WORKOUT SCREEN
========================================================= */

function showWorkoutScreen() {

    restScreen.classList.add("d-none");

    workoutCard.classList.remove("d-none");

    navigationButtons.classList.remove("d-none");
}


/* =========================================================
   SHOW REST SCREEN
========================================================= */

function showRestScreen() {

    workoutCard.classList.add("d-none");

    navigationButtons.classList.add("d-none");

    restScreen.classList.remove("d-none");
}


/* =========================================================
   LOAD WORKOUT
========================================================= */

function loadWorkout(index) {

    const workout = workouts[index];

    if (!workout) {
        return;
    }


    /* -----------------------------------------
       STOP EVERYTHING FROM PREVIOUS EXERCISE
    ----------------------------------------- */

    stopAllTimers();

    window.speechSynthesis.cancel();


    /* -----------------------------------------
       STATE
    ----------------------------------------- */

    currentWorkout = index;

    currentScreen = "workout";


    /* -----------------------------------------
       COUNTER
       
       IMPORTANT:
       This produces:
       Workout 1/10
       Workout 2/10
       ...
       Workout 10/10
    ----------------------------------------- */

    workoutNumber.textContent =
        `${index + 1}/${workouts.length}`;


    /* -----------------------------------------
       EXERCISE NAME
    ----------------------------------------- */

    exerciseName.textContent =
        workout.exercise;


    /* -----------------------------------------
       REPS / SECONDS
    ----------------------------------------- */

    if (Number(workout.seconds) > 0) {

        reps.textContent =
            `${workout.seconds} SEC`;

    } else {

        reps.textContent =
            workout.reps;
    }


    /* -----------------------------------------
       VIDEO
    ----------------------------------------- */

    const videoWrapper =
        video ? video.closest(".t2c-video-wrapper") : null;

    if (workout.video) {

        if (videoWrapper) videoWrapper.style.display = "block";
        videoSource.src = workout.video;
        video.load();

        video.play().catch(function(error) {
            console.log("Video autoplay blocked:", error);
        });

    } else {

        // Prevent the previous exercise's video from remaining on screen.
        if (video) {
            video.pause();
            video.removeAttribute("src");
        }
        if (videoSource) videoSource.removeAttribute("src");
        if (videoWrapper) videoWrapper.style.display = "none";
    }


    /* -----------------------------------------
       PREVIOUS BUTTON
    ----------------------------------------- */

    previousButton.disabled =
        index === 0;


    /* -----------------------------------------
       NEXT BUTTON
    ----------------------------------------- */

    if (index === workouts.length - 1) {

        nextButton.textContent =
            "Finish ✓";

        nextButton.classList.remove(
            "btn-primary"
        );

        nextButton.classList.add(
            "btn-success"
        );

    } else {

        nextButton.textContent =
            "Done →";

        nextButton.classList.remove(
            "btn-success"
        );

        nextButton.classList.add(
            "btn-primary"
        );
    }


    /* -----------------------------------------
       SHOW WORKOUT
    ----------------------------------------- */

    showWorkoutScreen();


    /* -----------------------------------------
       START TIME-BASED EXERCISE
    ----------------------------------------- */

    if (Number(workout.seconds) > 0) {

        startExerciseTimer();
    }
}


/* =========================================================
   EXERCISE TIMER
========================================================= */

function startExerciseTimer() {

    stopAllTimers();

    const workout = workouts[currentWorkout];

    if (!workout) {
        return;
    }

    exerciseTime =
        Number(workout.seconds);


    if (exerciseTime <= 0) {
        return;
    }


    reps.textContent =
        `${exerciseTime} SEC`;


    exerciseInterval =
        setInterval(function() {

            /* -----------------------------------------
               SAFETY
            ----------------------------------------- */

            if (currentScreen !== "workout") {

                clearInterval(exerciseInterval);
                exerciseInterval = null;

                return;
            }


            exerciseTime--;


            reps.textContent =
                `${exerciseTime} SEC`;


            /* -----------------------------------------
               COUNTDOWN
            ----------------------------------------- */

            if (
                exerciseTime <= 3 &&
                exerciseTime > 0
            ) {

                speakCountdown(exerciseTime);
            }


            /* -----------------------------------------
               EXERCISE FINISHED
            ----------------------------------------- */

            if (exerciseTime <= 0) {

                clearInterval(exerciseInterval);
                exerciseInterval = null;

                reps.textContent =
                    "DONE ✓";

                if (video) {
                    video.pause();
                }


                /* -------------------------------------
                   LAST EXERCISE
                ------------------------------------- */

                if (
                    currentWorkout ===
                    workouts.length - 1
                ) {

                    nextButton.textContent =
                        "Finish ✓";

                    return;
                }


                /* -------------------------------------
                   START REST
                ------------------------------------- */

                startRest();
            }

        }, 1000);
}


/* =========================================================
   START REST
========================================================= */

function startRest() {

    /* -----------------------------------------
       VERY IMPORTANT
       Kill every old timer first.
    ----------------------------------------- */

    stopAllTimers();

    window.speechSynthesis.cancel();


    /* -----------------------------------------
       CHANGE STATE
    ----------------------------------------- */

    currentScreen = "rest";


    /* -----------------------------------------
       REST UI
    ----------------------------------------- */

    restTitle.textContent =
        "REST";

    restMessage.textContent =
        "Take a short rest.";


    /* -----------------------------------------
       NEXT EXERCISE
    ----------------------------------------- */

    if (
        currentWorkout + 1 <
        workouts.length
    ) {

        nextExercise.textContent =
            `Your next exercise is ${
                workouts[currentWorkout + 1].exercise
            }`;

    } else {

        nextExercise.textContent =
            "Your workout is almost complete!";
    }


    /* -----------------------------------------
       REST TIME
    ----------------------------------------- */

    restTime =
        Number(
            workouts[currentWorkout].rest
        );


    restTimer.textContent =
        `${restTime} seconds`;


    /* -----------------------------------------
       SHOW REST SCREEN
    ----------------------------------------- */

    showRestScreen();


    /* -----------------------------------------
       SPEAK
    ----------------------------------------- */

    speak("Take a rest");


    /* -----------------------------------------
       NO REST
    ----------------------------------------- */

    if (restTime <= 0) {

        finishRest();

        return;
    }


    /* -----------------------------------------
       START REST TIMER
    ----------------------------------------- */

    restInterval =
        setInterval(function() {

            /* -------------------------------------
               SAFETY CHECK
            ------------------------------------- */

            if (currentScreen !== "rest") {

                clearInterval(restInterval);
                restInterval = null;

                return;
            }


            restTime--;


            restTimer.textContent =
                `${restTime} seconds`;


            /* -------------------------------------
               NEXT EXERCISE SPEECH
            ------------------------------------- */

            if (
                restTime === 7 &&
                currentWorkout + 1 <
                workouts.length
            ) {

                speak(
                    `Next exercise is ${
                        workouts[currentWorkout + 1].exercise
                    }`
                );
            }


            /* -------------------------------------
               3, 2, 1
            ------------------------------------- */

            if (
                restTime <= 3 &&
                restTime > 0
            ) {

                speakCountdown(restTime);
            }


            /* -------------------------------------
               REST FINISHED
            ------------------------------------- */

            if (restTime <= 0) {

                clearInterval(restInterval);
                restInterval = null;

                finishRest();
            }

        }, 1000);
}


/* =========================================================
   FINISH REST
========================================================= */

function finishRest() {

    /* -----------------------------------------
       STOP REST TIMER IMMEDIATELY
    ----------------------------------------- */

    if (restInterval !== null) {

        clearInterval(restInterval);
        restInterval = null;
    }


    /* -----------------------------------------
       STOP SPEECH
    ----------------------------------------- */

    window.speechSynthesis.cancel();


    /* -----------------------------------------
       MOVE TO NEXT WORKOUT
    ----------------------------------------- */

    const nextIndex =
        currentWorkout + 1;


    if (nextIndex >= workouts.length) {

        return;
    }


    /* -----------------------------------------
       LOAD NEXT WORKOUT
    ----------------------------------------- */

    currentWorkout = nextIndex;

    loadWorkout(currentWorkout);


    /* -----------------------------------------
       SPEAK NEXT EXERCISE
    ----------------------------------------- */

    setTimeout(function() {

        if (
            currentScreen === "workout" &&
            workouts[currentWorkout]
        ) {

            speak(
                workouts[currentWorkout].exercise
            );
        }

    }, 300);
}


/* =========================================================
   NEXT / DONE / FINISH
========================================================= */

nextButton.addEventListener(
    "click",
    function(event) {

        event.preventDefault();


        /* -----------------------------------------
           LAST WORKOUT
        ----------------------------------------- */

        if (
            currentWorkout ===
            workouts.length - 1
        ) {

            stopEverything();

            currentScreen = "finished";


            if (completeWorkoutForm) {

                completeWorkoutForm.submit();

            } else {

                console.error(
                    "completeWorkoutForm not found."
                );
            }

            return;
        }


        /* -----------------------------------------
           NORMAL DONE BUTTON
        ----------------------------------------- */

        if (video) {
            video.pause();
        }


        startRest();
    }
);


/* =========================================================
   PREVIOUS
========================================================= */

previousButton.addEventListener(
    "click",
    function(event) {

        event.preventDefault();


        if (currentWorkout <= 0) {

            return;
        }


        /* -----------------------------------------
           STOP EVERYTHING
        ----------------------------------------- */

        stopEverything();


        /* -----------------------------------------
           PREVIOUS WORKOUT
        ----------------------------------------- */

        currentWorkout--;


        loadWorkout(currentWorkout);


        /* -----------------------------------------
           SPEAK
        ----------------------------------------- */

        setTimeout(function() {

            if (workouts[currentWorkout]) {

                speak(
                    workouts[currentWorkout].exercise
                );
            }

        }, 300);
    }
);


/* =========================================================
   SKIP REST
========================================================= */

if (skipRest) {

    skipRest.addEventListener(
        "click",
        function(event) {

            event.preventDefault();


            /* -----------------------------------------
               ONLY SKIP DURING REST
            ----------------------------------------- */

            if (currentScreen !== "rest") {

                return;
            }


            /* -----------------------------------------
               STOP REST TIMER FIRST
            ----------------------------------------- */

            if (restInterval !== null) {

                clearInterval(restInterval);
                restInterval = null;
            }


            /* -----------------------------------------
               STOP SPEECH
            ----------------------------------------- */

            window.speechSynthesis.cancel();


            /* -----------------------------------------
               MOVE IMMEDIATELY
            ----------------------------------------- */

            finishRest();
        }
    );
}


/* =========================================================
   GET READY COUNTDOWN
========================================================= */

function startWorkoutCountdown() {

    if (
        !workouts ||
        workouts.length === 0
    ) {

        return;
    }


    /* -----------------------------------------
       STOP EVERYTHING
    ----------------------------------------- */

    stopEverything();


    /* -----------------------------------------
       STATE
    ----------------------------------------- */

    currentScreen = "ready";

    currentWorkout = 0;


    /* -----------------------------------------
       COUNTER
    ----------------------------------------- */

    workoutNumber.textContent =
        `0/${workouts.length}`;


    /* -----------------------------------------
       SHOW READY SCREEN
    ----------------------------------------- */

    workoutCard.classList.add("d-none");

    navigationButtons.classList.add("d-none");

    restScreen.classList.remove("d-none");


    /* -----------------------------------------
       GET READY
    ----------------------------------------- */

    restTitle.textContent =
        "GET READY";

    restMessage.textContent =
        "";


    nextExercise.textContent =
        `Your first exercise is ${
            workouts[0].exercise
        }`;


    /* -----------------------------------------
       TIMER
    ----------------------------------------- */

    readyCountdown = 10;

    restTimer.textContent =
        `${readyCountdown} seconds`;


    speak("Get ready");


    /* -----------------------------------------
       READY TIMER
    ----------------------------------------- */

    readyInterval =
        setInterval(function() {

            if (currentScreen !== "ready") {

                clearInterval(readyInterval);
                readyInterval = null;

                return;
            }


            readyCountdown--;


            restTimer.textContent =
                `${readyCountdown} seconds`;


            if (
                readyCountdown <= 3 &&
                readyCountdown > 0
            ) {

                speakCountdown(
                    readyCountdown
                );
            }


            /* -------------------------------------
               READY FINISHED
            ------------------------------------- */

            if (readyCountdown <= 0) {

                clearInterval(readyInterval);
                readyInterval = null;

                window.speechSynthesis.cancel();

                currentWorkout = 0;

                loadWorkout(0);


                setTimeout(function() {

                    if (
                        currentScreen === "workout"
                    ) {

                        speak(
                            workouts[0].exercise
                        );
                    }

                }, 300);
            }

        }, 1000);
}


/* =========================================================
   START
========================================================= */

if (
    workouts &&
    workouts.length > 0
) {

    startWorkoutCountdown();
}